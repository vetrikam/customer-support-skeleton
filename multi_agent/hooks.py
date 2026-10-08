"""One PreToolUse hook, registered once, against the refund tool.

Every agent that can call process_refund passes through it, so the ceiling is not
repeated in four prompts and not left to the tool alone. A denial is returned to the
agent as data (a business error with a reason), and the refund is never recorded.
"""

from __future__ import annotations

from agent.backend import get_order
from claude_agent_sdk import HookMatcher

from .ledger import Ledger

REFUND_CEILING = 500.00
REFUND_TOOL = "process_refund"


def make_refund_ceiling_hook(ledger: Ledger):
    async def refund_ceiling_hook(input_data, tool_use_id, context):
        tool_input = input_data.get("tool_input", {})
        amount = tool_input.get("amount_usd")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return {}  # not a number: the tool's own validation rejects it
        oid = str(tool_input.get("order_id", "")).strip().upper()
        order = get_order(oid)
        if order is None:
            return {}
        base = ledger.refunded_by_order.get(oid, order["already_refunded"])
        total_after = round(base + amount, 2)
        if total_after <= REFUND_CEILING:
            return {}
        reason = (
            f"Refund of ${amount:.2f} on {oid} would bring the total refunded to "
            f"${total_after:.2f}, above the ${REFUND_CEILING:.2f} ceiling. Not issued. "
            "Escalate to a human with escalate_ticket."
        )
        ledger.denials.append({
            "order_id": oid,
            "amount_usd": amount,
            "total_after": total_after,
            "agent": input_data.get("agent_type"),
            "reason": reason,
        })
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return refund_ceiling_hook


def build_hooks(ledger: Ledger, matcher: str = REFUND_TOOL) -> dict:
    """The one registration. Matches the refund tool for every agent.

    Offline runs use the bare tool name. Live runs pass the full MCP name, e.g.
    mcp__support__process_refund, because the SDK names MCP tools that way.
    """
    return {"PreToolUse": [HookMatcher(matcher=matcher, hooks=[make_refund_ceiling_hook(ledger)])]}
