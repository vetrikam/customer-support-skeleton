"""Coordinator for Assignment 2.

Decomposes the ticket, delegates each concern to one specialist, runs a coverage check
against the ticket, and writes the report. Each subagent gets its facts explicitly in
its prompt. Nothing is inherited from this conversation.

Offline stand-in: `decompose` is a rule table that mirrors what each coordinator prompt
says. In a live run the model reads the prompt and makes the same assignments.
"""

from __future__ import annotations

import difflib
import pathlib

from agent.backend import ORDERS

from .fixtures import CONCERNS
from .hooks import build_hooks
from .ledger import Ledger
from .results import Outcome
from .runtime import Worker
from .specialists import RUNNERS, build_prompt
from .specs import SPECIALIST_SPECS

PROMPT_DIR = pathlib.Path(__file__).parent / "prompts"

KIND_TO_AGENT = {
    "refund": "refund-processor",
    "investigate": "order-investigator",
    "policy": "policy-analyst",
    "escalate": "escalation-writer",
}
# What the BEFORE prompt names: only refunds and return-window questions.
BEFORE_KINDS = {"refund", "policy"}
SUMMARY = {c["id"]: c["summary"] for c in CONCERNS}


def load_prompt(version: str) -> str:
    return (PROMPT_DIR / f"coordinator_{version}.md").read_text(encoding="utf-8")


def decompose(version: str, concerns=CONCERNS) -> dict:
    """Map concern id -> specialist name, for the given coordinator prompt version."""
    kinds = BEFORE_KINDS if version == "before" else {c["kind"] for c in concerns}
    return {c["id"]: KIND_TO_AGENT[c["kind"]] for c in concerns if c["kind"] in kinds}


def coverage_check(concerns, assignments: dict) -> list:
    """Checked against the TICKET's concerns, not the coordinator's own plan."""
    return [c["id"] for c in concerns if c["id"] not in assignments]


def facts_for(cid: str, outcomes: dict) -> dict:
    """Every fact a specialist needs, written out. Nothing is inherited."""
    if cid == "C1":
        o = ORDERS["ORD-123"]
        return {
            "customer_id": o["customer_id"], "order_id": o["order_id"], "order_status": o["status"],
            "amount_usd": o["total"],
            "reason": "Ceramic pour-over kettle arrived with a crack in the spout; customer reports it leaks.",
        }
    if cid == "C2":
        o = ORDERS["ORD-789"]
        return {"customer_id": o["customer_id"], "order_id": o["order_id"]}
    if cid == "C3":
        o = ORDERS["ORD-789"]
        return {"order_id": o["order_id"], "order_status": o["status"], "window_starts": "delivery"}
    if cid == "C4":
        o = ORDERS["ORD-789"]
        c2 = outcomes.get("C2")
        note = ("The duplicate-charge investigation (C2) is complete."
                if c2 and c2.status == "resolved"
                else "The duplicate-charge investigation (C2) is NOT complete: the payments lookup failed.")
        return {
            "customer_id": o["customer_id"], "order_id": o["order_id"], "amount_usd": o["total"],
            "root_cause": ("Customer reports a double charge on ORD-789 and threatens a chargeback "
                           f"if it is not resolved by Friday. {note}"),
            "recommended_action": "Refund the duplicate charge once confirmed, and reply to the customer before Friday.",
        }
    raise KeyError(cid)


def denial_facts(facts: dict, outcome: Outcome) -> dict:
    """Facts for the escalation writer after the hook refuses a refund."""
    return {
        "customer_id": facts["customer_id"],
        "order_id": facts["order_id"],
        "amount_usd": facts["amount_usd"],
        "root_cause": f"Refund refused by the ceiling hook: {outcome.findings[0].evidence}",
        "recommended_action": "Review the refund against the $500 ceiling and approve, reduce, or decline it.",
    }


async def run_agent(cid, agent, facts, ledger: Ledger, hooks) -> Outcome:
    task = f"{cid}: {SUMMARY.get(cid, cid)}"
    prompt = build_prompt(agent, facts, task)
    worker = Worker(agent, SPECIALIST_SPECS[agent].tools, ledger, hooks)
    outcome = await RUNNERS[agent](cid, facts, worker)
    outcome.prompt = prompt
    return outcome


async def resolve(cid, agent, facts, ledger: Ledger, hooks) -> Outcome:
    outcome = await run_agent(cid, agent, facts, ledger, hooks)
    if outcome.failure and outcome.failure.isRetryable:
        ledger.trace(f"coordinator: {cid} failed ({outcome.failure.errorCategory}); retrying once")
        outcome = await run_agent(cid, agent, facts, ledger, hooks)
    if outcome.status == "escalated" and agent == "refund-processor":
        ledger.trace(f"coordinator: {cid} refund refused by the hook; handing to escalation-writer")
        esc = await run_agent(cid, "escalation-writer", denial_facts(facts, outcome), ledger, hooks)
        outcome.findings = outcome.findings + esc.findings
        outcome.status = esc.status
        outcome.failure = esc.failure
    return outcome


async def run_ticket(ledger: Ledger, log: list | None = None):
    log = log if log is not None else []
    hooks = build_hooks(ledger)
    outcomes: dict = {}

    round1 = decompose("before")
    gaps = coverage_check(CONCERNS, round1)
    log.append(("Round 1: coordinator prompt BEFORE the coverage fix", round1, gaps))
    for cid in sorted(round1):
        outcomes[cid] = await resolve(cid, round1[cid], facts_for(cid, outcomes), ledger, hooks)

    after = decompose("after")
    redelegated = {cid: after[cid] for cid in gaps}
    remaining = coverage_check(CONCERNS, {**round1, **redelegated})
    log.append(("Round 2: coordinator prompt AFTER the fix, gaps only", redelegated, remaining))
    for cid in sorted(redelegated):
        outcomes[cid] = await resolve(cid, redelegated[cid], facts_for(cid, outcomes), ledger, hooks)
    return outcomes, log


def render_report(outcomes: dict, concerns=CONCERNS) -> str:
    lines = []
    handled, unresolved = [], []
    for c in concerns:
        cid = c["id"]
        o = outcomes.get(cid)
        if o is None:
            lines.append(f"{cid} NOT ASSIGNED: {c['summary']}")
            unresolved.append(cid)
            continue
        if o.failure:
            f = o.failure
            lines.append(f"{cid} UNRESOLVED: {c['summary']}")
            lines.append(f"   failure: {f.errorCategory} (retryable: {f.isRetryable}). {f.message}")
            lines.append(f"   attempted: {'; '.join(f.attempted) or 'nothing'}")
            lines.append(f"   partial results: {'; '.join(f.partial) or 'none'}")
            lines.append(f"   alternatives: {'; '.join(f.alternatives) or 'none'}")
            unresolved.append(cid)
        else:
            handled.append(cid)
        for fd in o.findings:
            lines.append(f"{cid} {o.status.upper()}: {fd.claim}")
            lines.append(f"   evidence: {fd.evidence}")
            lines.append(f"   source: {fd.source}")
    lines.append("")
    lines.append(f"Handled or escalated: {', '.join(handled) or 'none'}.")
    lines.append(f"Unresolved: {', '.join(unresolved) or 'none'}.")
    return "\n".join(lines)
