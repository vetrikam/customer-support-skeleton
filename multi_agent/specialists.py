"""The four specialists. Each sees only its prompt's facts, never the coordinator's state.

`build_prompt` is the whole context a subagent starts with: its role, its facts, its task.
"""

from __future__ import annotations

from agent.errors import VALIDATION

from .results import Failure, Finding, Outcome
from .runtime import Worker
from .specs import SPECIALIST_SPECS


def build_prompt(agent_name: str, facts: dict, task: str) -> str:
    fact_block = "\n".join(f"- {k}: {v}" for k, v in facts.items())
    return f"{SPECIALIST_SPECS[agent_name].prompt}\n\nFacts (your only source of truth):\n{fact_block}\n\nTask: {task}"


def _failure(cid, agent, res, attempted, partial, alternatives) -> Outcome:
    failure = Failure(
        concern_id=cid,
        agent=agent,
        errorCategory=res["errorCategory"],
        isRetryable=res["isRetryable"],
        message=res["message"],
        attempted=attempted,
        partial=partial,
        alternatives=alternatives,
    )
    return Outcome(cid, agent, failure=failure, status="unresolved")


async def order_investigator(cid, facts, w: Worker) -> Outcome:
    oid = facts["order_id"]
    attempted = [f"lookup_order({oid})"]
    look = await w.call("lookup_order", {"order_id": oid})
    if not look["ok"]:
        return _failure(cid, w.name, look, attempted, [], ["retry lookup_order", "escalate for human review"])
    partial = [f"order {oid}: status {look['status']}, total ${look['total']:.2f}"]
    attempted.append(f"find_charges({oid})")
    res = await w.call("find_charges", {"order_id": oid, "customer_id": facts["customer_id"]})
    if not res["ok"]:
        return _failure(
            cid, w.name, res, attempted, partial,
            ["retry find_charges (retryable)", "escalate for manual billing review, with the order facts above"],
        )
    charges = res["charges"]
    if not charges:
        f = Finding(cid, f"No charges are recorded on {oid}, so there is no duplicate charge.",
                    "find_charges returned an empty charge list: a valid result, not an error.",
                    f"payments:charges/{oid}")
    else:
        amounts = {c["amount_usd"] for c in charges}
        if len(charges) > 1 and len(amounts) == 1:
            f = Finding(cid, f"{oid} was charged {len(charges)} times at ${charges[0]['amount_usd']:.2f} each: a likely duplicate.",
                        "charge IDs " + ", ".join(c["charge_id"] for c in charges) + f", dated {charges[0]['date']}.",
                        f"payments:charges/{oid}")
        else:
            f = Finding(cid, f"{oid} has {len(charges)} charge(s) with no duplicate amount.",
                        "charge IDs " + ", ".join(c["charge_id"] for c in charges) + ".",
                        f"payments:charges/{oid}")
    return Outcome(cid, w.name, [f], status="resolved")


async def policy_analyst(cid, facts, w: Worker) -> Outcome:
    oid = facts["order_id"]
    pol = await w.call("read_policy", {})
    if not pol["ok"]:
        return _failure(cid, w.name, pol, ["read_policy()"], [], ["retry read_policy"])
    days, starts, src = pol["return_window_days"], pol["window_starts"], pol["source"]
    status = facts.get("order_status")
    if status is None:
        # The hedged answer: the policy is known, the order's status is not.
        claim = f"A {days}-day return window probably applies to {oid}, but I cannot confirm it."
        evidence = (f"The policy window runs {days} days from {starts}. My facts do not include the "
                    "order's status, so I cannot tell whether the window has started.")
    elif status != "delivered":
        claim = f"No return window has started for {oid}. It opens on delivery and lasts {days} days."
        evidence = f"Order status is '{status}' (given in my facts). The window starts on {starts}."
    else:
        claim = f"{oid} can be returned within {days} days of delivery."
        evidence = "Order status is 'delivered' (given in my facts)."
    return Outcome(cid, w.name, [Finding(cid, claim, evidence, src)], status="resolved")


async def refund_processor(cid, facts, w: Worker) -> Outcome:
    oid = facts["order_id"]
    res = await w.call("process_refund", {
        "order_id": oid,
        "customer_id": facts["customer_id"],
        "amount_usd": facts["amount_usd"],
        "reason": facts["reason"],
    })
    if res["ok"]:
        f = Finding(cid, f"Refunded ${res['amount_usd']:.2f} on {oid}. Refund {res['refund_id']} settles in {res['settles_in_days']} days.",
                    f"process_refund succeeded. Reason on file: {facts['reason']}",
                    f"refund:{res['refund_id']}")
        return Outcome(cid, w.name, [f], status="resolved")
    if res.get("denied_by"):
        # A denial is data, not a crash. The coordinator hands it to the escalation writer.
        f = Finding(cid, f"Refund of ${facts['amount_usd']:.2f} on {oid} was NOT issued. It needs a human.",
                    res["message"], res["denied_by"])
        return Outcome(cid, w.name, [f], status="escalated")
    hint = "fix the arguments and retry" if res["errorCategory"] == VALIDATION else "explain the refusal to the customer"
    return _failure(cid, w.name, res, [f"process_refund({oid})"], [], [hint])


async def escalation_writer(cid, facts, w: Worker) -> Outcome:
    res = await w.call("escalate_ticket", {
        "customer_id": facts["customer_id"],
        "order_id": facts["order_id"],
        "amount_usd": facts.get("amount_usd"),
        "root_cause": facts["root_cause"],
        "recommended_action": facts["recommended_action"],
        "concern_id": cid,
    })
    if not res["ok"]:
        return _failure(cid, w.name, res, ["escalate_ticket"], [], ["fix the ticket fields and retry"])
    f = Finding(cid, f"Escalated to {res['queue']} as {res['ticket_id']}. A person replies within {res['response_within_hours']} hours.",
                facts["root_cause"], f"ticket:{res['ticket_id']}")
    return Outcome(cid, w.name, [f], status="escalated")


RUNNERS = {
    "order-investigator": order_investigator,
    "policy-analyst": policy_analyst,
    "refund-processor": refund_processor,
    "escalation-writer": escalation_writer,
}
