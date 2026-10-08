#!/usr/bin/env python3
"""Assignment 2 demo, offline. No API key.

The real code runs: AgentDefinition specs from claude-agent-sdk, the tool-scoping
check, the single PreToolUse refund-ceiling hook, structured findings and failures,
and the coverage rounds. The model's choices are rule-based stand-ins (see
multi_agent/coordinator.py decompose and multi_agent/specialists.py). The live run
replaces them with the model reading the same prompts.

    python run_multi.py          # prints and writes transcripts/multi-agent.md
    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import asyncio
import difflib
import json
import pathlib

from multi_agent.coordinator import (
    decompose, load_prompt, render_report, resolve, run_ticket, coverage_check,
)
from multi_agent.fixtures import CONCERNS, TICKET
from multi_agent.hooks import build_hooks
from multi_agent.ledger import Ledger
from multi_agent.runtime import Worker
from multi_agent.specialists import build_prompt, policy_analyst
from multi_agent.specs import SPECIALIST_SPECS, unscoped

ROOT = pathlib.Path(__file__).parent


async def demo_scope(out):
    out.append("\n## 1. Tool scoping: explicit tools list vs. tools field omitted\n")
    led = Ledger()
    scoped = Worker("policy-analyst", SPECIALIST_SPECS["policy-analyst"].tools, led, build_hooks(led))
    r = await scoped.call("process_refund", {"order_id": "ORD-321", "customer_id": "CUST-002",
                                              "amount_usd": 10.00, "reason": "scope test"})
    out.append(f"- policy-analyst (tools={SPECIALIST_SPECS['policy-analyst'].tools}) calls process_refund: "
               f"ok={r['ok']}, {r['errorCategory']}: {r['message']}")
    led2 = Ledger()
    omitted = unscoped("policy-analyst")
    over = Worker("policy-analyst", omitted.tools, led2, build_hooks(led2))
    r2 = await over.call("process_refund", {"order_id": "ORD-321", "customer_id": "CUST-002",
                                             "amount_usd": 10.00, "reason": "scope test (throwaway ledger)"})
    out.append(f"- same agent with tools omitted (tools={omitted.tools}) calls process_refund: "
               f"ok={r2['ok']}, refund={r2.get('refund_id')}. The subagent could refund.")


async def demo_hedge(out):
    out.append("\n## 2. Context passing: the hedged answer, then the fixed prompt\n")
    led = Ledger()
    w = Worker("policy-analyst", SPECIALIST_SPECS["policy-analyst"].tools, led, build_hooks(led))
    bare = {"order_id": "ORD-789"}
    full = {"order_id": "ORD-789", "order_status": "in_transit", "window_starts": "delivery"}
    task = "C3: Return window for ORD-789 after arrival: policy read"
    out.append("### Prompt given WITHOUT the order status\n```text\n" + build_prompt("policy-analyst", bare, task) + "\n```")
    o1 = await policy_analyst("C3", bare, w)
    out.append(f"Answer: {o1.findings[0].claim}")
    out.append("### Same subagent, with the status stated in its prompt\n```text\n" + build_prompt("policy-analyst", full, task) + "\n```")
    o2 = await policy_analyst("C3", full, w)
    out.append(f"Answer: {o2.findings[0].claim}")


async def demo_access(out):
    out.append("\n## 3. Access failure vs. valid empty result\n")
    led = Ledger()
    w = Worker("order-investigator", SPECIALIST_SPECS["order-investigator"].tools, led, build_hooks(led))
    a = await w.call("find_charges", {"order_id": "ORD-456", "customer_id": "CUST-001"})
    out.append(f"- Alice asks about Bob's ORD-456: ok={a['ok']}, errorCategory={a['errorCategory']}, "
               f"isRetryable={a['isRetryable']}. Access failure: the data exists but this caller may not see it.")
    b = await w.call("find_charges", {"order_id": "ORD-321", "customer_id": "CUST-002"})
    out.append(f"- Bob asks about his own ORD-321 (no charges on file): ok={b['ok']}, charges={b.get('charges')}. "
               "Valid empty: the lookup worked and found nothing.")


async def demo_ceiling(out):
    out.append("\n## 4. Refund ceiling: the hook denies, the log stays empty, the agent escalates\n")
    led = Ledger()
    hooks = build_hooks(led)
    facts = {"customer_id": "CUST-002", "order_id": "ORD-456", "amount_usd": 899.00,
             "reason": "Professional stand mixer arrived with the housing crushed on one side."}
    out.append("Request: refund $899.00 on ORD-456 (over the $500 ceiling, and outside the 30-day window).")
    outcome = await resolve("C1", "refund-processor", facts, led, hooks)
    out.append(f"- refunds recorded: {led.refunds or 'none (log is empty)'}")
    out.append(f"- hook denials: {[d['reason'] for d in led.denials]}")
    out.append(f"- tickets written: {[t['ticket_id'] + ' ' + t['root_cause'][:70] + '...' for t in led.tickets]}")
    out.append("\n```text\n" + render_report({"C1": outcome}, [CONCERNS[0]]) + "\n```")
    return led


async def demo_ticket(out):
    out.append("\n## 5. The full ticket: four concerns, coverage rounds, one failure\n")
    out.append("Ticket:\n> " + TICKET)
    ledger = Ledger(charge_failures_left=2)  # the payments lookup fails on the first call and its retry
    outcomes, log = await run_ticket(ledger, [])
    for title, assigned, gaps in log:
        out.append(f"\n### {title}")
        out.append(f"- assigned: {assigned}")
        out.append(f"- coverage gaps after this round: {gaps or 'none'}")
    out.append("\n### Coordinator prompt: diff from before to after the fix")
    diff = difflib.unified_diff(load_prompt("before").splitlines(), load_prompt("after").splitlines(),
                                "coordinator_before.md", "coordinator_after.md", lineterm="")
    out.append("```diff\n" + "\n".join(diff) + "\n```")
    out.append("\n### Report\n```text\n" + render_report(outcomes) + "\n```")
    out.append("\n### What each subagent was given\n")
    for cid in sorted(outcomes):
        out.append(f"**{cid}**\n```text\n{outcomes[cid].prompt}\n```")
    out.append("\n### Tool trace\n```text\n" + "\n".join(ledger.trace_lines) + "\n```")
    out.append(f"\nRefund log at the end: {json.dumps(ledger.refunds)}")
    out.append(f"Tickets at the end: {[t['ticket_id'] for t in ledger.tickets]}")


async def main():
    out = [
        "# Assignment 2 transcript (offline run)",
        "Generated by run_multi.py. The tools, scoping, hook, and output contract are the real code. "
        "The model's choices are rule-based stand-ins (see coordinator.decompose and specialists).",
    ]
    await demo_scope(out)
    await demo_hedge(out)
    await demo_access(out)
    await demo_ceiling(out)
    await demo_ticket(out)
    text = "\n".join(out) + "\n"
    path = ROOT / "transcripts" / "multi-agent.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
