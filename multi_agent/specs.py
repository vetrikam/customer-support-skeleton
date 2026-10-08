"""The four specialists as AgentDefinitions. Each names ONLY the tools its role needs.

`unscoped()` builds the same agent with the tools field left out. The SDK then gives
the subagent every tool, including ones its own prompt forbids. run_multi.py shows both.
"""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition

SPECIALIST_SPECS = {
    "order-investigator": AgentDefinition(
        description="Looks up one order and its charge history. Read-only. Use for duplicate or billing questions.",
        prompt=(
            "You investigate billing on one order. You know only the facts in your prompt. "
            "Report what the payment records show, and say plainly when they show nothing."
        ),
        tools=["lookup_order", "find_charges"],
    ),
    "policy-analyst": AgentDefinition(
        description="Answers one return-policy question about one order. Read-only.",
        prompt=(
            "You answer one return-policy question about one order. You know only the facts in "
            "your prompt. Apply the policy to the order status given in your facts."
        ),
        tools=["read_policy"],
    ),
    "refund-processor": AgentDefinition(
        description="Refunds one delivered order at its order total. Use only for refund requests.",
        prompt=(
            "You issue one refund. First look up the order with lookup_order, then refund it. "
            "Use the order total from lookup_order as the amount when the order is one item (the item "
            "field names it). Use the customer_id from lookup_order. If the order holds more than one item, "
            "or the record does not settle the amount, do not refund: report that and stop. If a refund is "
            "refused, report the refusal exactly as given. Do not look for another route."
        ),
        tools=["lookup_order", "process_refund"],
    ),
    "escalation-writer": AgentDefinition(
        description="Writes one handoff ticket for a human. Use when a concern needs a person.",
        prompt=(
            "You write one escalation ticket for a human who has no access to this conversation. "
            "Use only the facts in your prompt: customer, order, amount, root cause and recommended action."
        ),
        tools=["escalate_ticket"],
    ),
}


def unscoped(name: str) -> AgentDefinition:
    """The same agent with the tools field LEFT OUT. The SDK then grants every tool."""
    spec = SPECIALIST_SPECS[name]
    return AgentDefinition(description=spec.description, prompt=spec.prompt)
