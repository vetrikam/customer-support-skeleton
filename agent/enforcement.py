"""The hard rules, enforced in code. ONE IS BUILT FOR YOU. THE OTHER IS TASK 4.

This module is a pre-execution interception hook. It runs between "Claude asked
for this tool" and "the tool function is called". If a check fails, the handler
is never invoked, so there is no code path on which the blocked thing happens,
whatever the model was talked into asking for.

    RULE 1  No order operation before identity verification.   <- WORKED EXAMPLE
    RULE 2  No refund above $500.00 without a human.           <- cumulative per order

Neither rule may appear in the system prompt. That is not a style preference: a
prompt instruction is a probability, a gate is a guarantee, and you have to argue
that difference in reflection question 1. The way you earn the right to that
argument is tests/test_enforcement.py, where you run the whole agent under a
system prompt that explicitly orders it to break both rules and assert that both
hold anyway.
"""

from __future__ import annotations

import math

from . import backend
from .errors import BUSINESS, PERMISSION, VALIDATION, fail
from .session import Session

REFUND_CEILING_USD = 500.00

# Tools that may not run until identity is verified. Additive by design: a new
# money-touching tool gets added here, not trusted to the prompt.
IDENTITY_GATED_TOOLS = frozenset({"lookup_order", "process_refund"})


def intercept(tool_name: str, tool_input: dict, session: Session) -> dict | None:
    """Returns None to allow the call, or an error envelope to block it.
    Blocking means the handler is never called."""
    blocked = _check(tool_name, tool_input, session)
    session.log(
        event="gate",
        tool=tool_name,
        decision="blocked" if blocked else "allowed",
        rule=blocked.get("rule") if blocked else None,
    )
    return blocked


def _check(tool_name: str, tool_input: dict, session: Session) -> dict | None:
    # ---- RULE 1: identity gate. WORKED EXAMPLE, read it closely ----------
    #
    # Note what it reads: session.verified_customer_id, which only a successful
    # get_customer call can write. It does not read the conversation, so a model
    # that announces "I have already verified this customer as CUST-1002" moves
    # nothing. There is a test for that.
    #
    # Note also which tools are NOT in the set. escalate_to_human is ungated on
    # purpose, so an explicit request for a human is honoured immediately even
    # from a caller who never verified.
    if tool_name in IDENTITY_GATED_TOOLS and not session.verified_customer_id:
        return fail(
            PERMISSION,
            f"Blocked: '{tool_name}' cannot run before the caller's identity is verified. "
            "Call get_customer with the customer's email first, then retry this call.",
            rule="identity_gate",
        )

    if tool_name != "process_refund":
        return None

    # ---- RULE 2: the refund ceiling --------------------------------------
    order_id = str(tool_input.get("order_id") or "").strip().upper()
    amount = tool_input.get("amount_usd")

    # 2a. The amount must be a real, positive number. bool is excluded explicitly
    #     because isinstance(True, int) is True in Python.
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not math.isfinite(amount)
    ):
        return fail(
            VALIDATION,
            f"amount_usd must be a number of dollars such as 149.99; got {amount!r}. "
            "Send it as a JSON number, not a string.",
            rule="refund_amount",
        )
    if amount <= 0:
        return fail(
            VALIDATION,
            f"amount_usd must be greater than zero; got {amount}.",
            rule="refund_amount",
        )

    # 2b. Ownership is an authorisation question, so it lives in the gate.
    #     An unknown order is left to the handler (validation).
    order = backend.get_order(order_id)
    if order is not None and order["customer_id"] != session.verified_customer_id:
        return fail(
            PERMISSION,
            f"Order {order_id} does not belong to the verified customer. Do not refund "
            "it and do not disclose anything about it.",
            rule="order_ownership",
        )

    # 2c + 2d. The ceiling, CUMULATIVE per order: what the order already carried,
    #     plus everything refunded on it in this session, plus this request. The
    #     number comes from session state and the backend, never from the
    #     conversation, so a split refund or a forgetful summary cannot move it.
    if order is not None:
        already = float(order.get("already_refunded") or 0.0)
        this_session = session.refunded_by_order.get(order["order_id"], 0.0)
        total_after = round(already + this_session + float(amount), 2)
        if total_after > REFUND_CEILING_USD:
            return fail(
                BUSINESS,
                f"A refund of ${float(amount):.2f} on {order['order_id']} would bring the "
                f"total refunded on this order to ${total_after:.2f}, above the "
                f"${REFUND_CEILING_USD:.2f} limit for refunds issued without approval. "
                "Tell the customer a specialist must approve this refund, then call "
                "escalate_to_human with reason_code 'over_refund_ceiling', the order_id "
                "and the full amount. Do NOT offer a reduced refund that fits under the "
                "limit, and do NOT split it into smaller refunds.",
                rule="refund_ceiling",
            )

    return None
