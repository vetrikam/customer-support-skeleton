"""Tool implementations.

get_customer and lookup_order are the skeleton's worked examples; process_refund
and escalate_to_human follow the same conventions.

CONVENTIONS THE WORKED EXAMPLES FOLLOW, AND YOURS SHOULD TOO

1. No exceptions escape. A tool that raises hands Claude a stack trace, which is
   both a bad prompt and a leak. Everything comes back as an envelope from
   errors.py.

2. Results are lean. Every tool_result is re-sent on every later turn, so each
   field is paid for once per remaining turn. Return what the agent needs to
   decide what to do next, and nothing else. Notice that get_customer does not
   echo back the email it was just given.
"""

from __future__ import annotations

import os
import re

from . import backend
from .errors import BUSINESS, PERMISSION, TRANSIENT, VALIDATION, fail, ok
from .session import Session
from .tools import REASON_CODES

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ORDER_ID_RE = re.compile(r"^ORD-\d{3,}$")
SETTLEMENT_DAYS = 5
ESCALATION_SLA_HOURS = 4


# ---------------------------------------------------------------------------
# WORKED EXAMPLE 1: identity verification
# ---------------------------------------------------------------------------
def get_customer(session: Session, email: str = "") -> dict:
    email = str(email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return fail(
            VALIDATION,
            f"'{email}' is not a valid email address. Ask the customer for the address "
            "their order was placed under.",
        )
    customer = backend.find_customer_by_email(email)
    if customer is None:
        return fail(
            VALIDATION,
            f"No account found for {email}. Ask the customer whether they ordered under a "
            "different address.",
        )
    # This assignment is the identity gate's only key. Nothing the model SAYS can
    # set it; only this line, on a real match.
    session.verified_customer_id = customer["customer_id"]
    session.verified_customer_name = customer["name"]
    session.log(event="verified", customer_id=customer["customer_id"])

    return ok(
        customer_id=customer["customer_id"],
        name=customer["name"],
        tier=customer["tier"],
        verified=True,
    )


# ---------------------------------------------------------------------------
# WORKED EXAMPLE 2: an order lookup that shows all four error categories in use
# ---------------------------------------------------------------------------
def lookup_order(session: Session, order_id: str = "") -> dict:
    order_id = str(order_id or "").strip().upper()
    if not ORDER_ID_RE.match(order_id):
        return fail(
            VALIDATION,
            f"'{order_id}' is not a valid order ID. The format is 'ORD-' followed by at "
            "least three digits, e.g. 'ORD-123'. Ask the customer for the full ID rather "
            "than adding a prefix yourself.",
        )

    # Seeded flake: the first attempt at certain orders fails transiently, so your
    # agent's retry behaviour is observable rather than something you assert in prose.
    attempts = session.lookup_attempts.get(order_id, 0) + 1
    session.lookup_attempts[order_id] = attempts
    if order_id in backend.FLAKY_ORDERS and attempts == 1:
        return fail(
            TRANSIENT,
            "The order service did not respond in time (gateway timeout). This is a "
            "temporary fault - retry this call once with the same order ID.",
        )

    order = backend.get_order(order_id)
    if order is None:
        return fail(VALIDATION, f"No order {order_id} exists.")
    if order["customer_id"] != session.verified_customer_id:
        return fail(
            PERMISSION,
            f"Order {order_id} belongs to a different customer. Do not disclose anything "
            "about it.",
        )

    result = ok(
        order_id=order["order_id"],
        item=order["item"],
        total=order["total"],
        status=order["status"],
        days_since_delivery=order["days_since_delivery"],
        return_window_open=backend.return_window_open(order),
        already_refunded=order["already_refunded"],
    )
    if order.get("carrier_note"):
        result["carrier_note"] = order["carrier_note"]
    return result


# ---------------------------------------------------------------------------
# FAIL_MODE: deterministic demo of the four error categories
# ---------------------------------------------------------------------------
# Set FAIL_MODE=transient|validation|business|permission before a live run and the
# FIRST process_refund call in that session returns that category instead of
# running. The point is to watch the agent do something different for each:
#
#   transient   retries the identical call, which then succeeds
#   validation  fixes its own argument (a vaguer reason) and calls again
#   business    stops, explains to the customer, escalates; does not retry
#   permission  re-verifies with get_customer, then retries
FAIL_MODES = (TRANSIENT, VALIDATION, BUSINESS, PERMISSION)


def _injected_failure(session: Session, tool: str) -> dict | None:
    mode = os.environ.get("FAIL_MODE", "").strip().lower()
    if mode not in FAIL_MODES or tool in session.injected_failures:
        return None
    session.injected_failures.add(tool)
    session.log(event="injected_failure", tool=tool, category=mode)
    if mode == TRANSIENT:
        return fail(
            TRANSIENT,
            "The payment processor did not respond in time. This is temporary - retry "
            "this call once with the same arguments.",
        )
    if mode == VALIDATION:
        return fail(
            VALIDATION,
            "Finance rejected the reason as too vague to audit. Rewrite `reason` as one "
            "sentence naming the item and the specific defect, then call again.",
        )
    if mode == BUSINESS:
        return fail(
            BUSINESS,
            "Refunds to this customer's original payment method are on hold because the "
            "card was reissued, so this refund cannot be sent automatically. A "
            "specialist can issue it to the new card. Do not retry; escalate with "
            "reason_code 'policy_exception'.",
        )
    # PERMISSION: the verification has expired, so the gate will block until the
    # customer is verified again. The session flag is what the gate reads.
    session.verified_customer_id = None
    return fail(
        PERMISSION,
        "The customer's verification expired before the refund could be authorised. "
        "Call get_customer with their email again, then retry the refund.",
    )


# ---------------------------------------------------------------------------
# process_refund
# ---------------------------------------------------------------------------
def process_refund(
    session: Session, order_id: str = "", amount_usd: float = 0.0, reason: str = ""
) -> dict:
    """Order-level business policy. Identity, ownership and the $500 ceiling have
    already been enforced by enforcement.intercept() before this runs."""
    injected = _injected_failure(session, "process_refund")
    if injected:
        return injected

    order_id = str(order_id or "").strip().upper()
    if not ORDER_ID_RE.match(order_id):
        return fail(
            VALIDATION,
            f"'{order_id}' is not a valid order ID. Use the 'ORD-123' form from "
            "lookup_order.",
        )
    order = backend.get_order(order_id)
    if order is None:
        return fail(VALIDATION, f"No order {order_id} exists.")

    # Defensive: the gate checks this too, but a handler must not trust its caller.
    if (
        isinstance(amount_usd, bool)
        or not isinstance(amount_usd, (int, float))
        or amount_usd <= 0
    ):
        return fail(VALIDATION, f"amount_usd must be a positive number; got {amount_usd!r}.")
    amount = round(float(amount_usd), 2)

    reason = str(reason or "").strip()
    if len(reason) < 15 or len(reason.split()) < 4:
        return fail(
            VALIDATION,
            f"The reason '{reason}' is too thin for finance to audit. Describe the item "
            "and the specific defect in one sentence, e.g. 'Kettle arrived with a cracked "
            "spout that leaks'.",
        )

    if order["status"] != "delivered":
        return fail(
            BUSINESS,
            f"Order {order_id} has not been delivered yet (status: {order['status']}), "
            "so it cannot be refunded. Once it arrives, the customer can request a return "
            f"within {backend.RETURN_WINDOW_DAYS} days.",
        )
    if not backend.return_window_open(order):
        return fail(
            BUSINESS,
            f"Order {order_id} was delivered {order['days_since_delivery']} days ago, "
            f"outside our {backend.RETURN_WINDOW_DAYS}-day return window, so it cannot be "
            "refunded automatically. A specialist can review it as an exception: "
            "escalate with reason_code 'policy_exception'.",
        )

    refunded_so_far = round(
        float(order.get("already_refunded") or 0.0)
        + session.refunded_by_order.get(order_id, 0.0),
        2,
    )
    still_owed = round(order["total"] - refunded_so_far, 2)
    if amount > still_owed:
        return fail(
            BUSINESS,
            f"The most that can be refunded on {order_id} is ${still_owed:.2f} (order "
            f"total ${order['total']:.2f}, already refunded ${refunded_so_far:.2f}). "
            f"${amount:.2f} is more than that.",
        )

    # Success: record against the session FIRST - the ceiling depends on it.
    session.refunded_by_order[order_id] = round(
        session.refunded_by_order.get(order_id, 0.0) + amount, 2
    )
    refund_id = session.next_refund_id()
    session.log(
        event="refund",
        refund_id=refund_id,
        order_id=order_id,
        amount_usd=amount,
        customer_id=session.verified_customer_id,
        reason=reason,
    )
    # Lean: exactly what the agent reads to the customer. No processor IDs, no
    # timestamps, no echo of the reason it just sent.
    return ok(
        refund_id=refund_id,
        order_id=order_id,
        amount_usd=amount,
        settles_in_days=SETTLEMENT_DAYS,
    )


# ---------------------------------------------------------------------------
# escalate_to_human
# ---------------------------------------------------------------------------
QUEUE_BY_REASON = {
    "over_refund_ceiling": "refund-approvals",
    "policy_exception": "refund-approvals",
    "suspected_fraud": "trust-and-safety",
    "customer_requested_human": "customer-care",
    "unresolvable": "customer-care",
}
ORDER_REASONS = {"over_refund_ceiling", "policy_exception"}
MIN_SUMMARY_CHARS = 40


def _too_thin(text: str, min_chars: int, min_words: int) -> bool:
    text = str(text or "").strip()
    return len(text) < min_chars or len(text.split()) < min_words


def escalate_to_human(
    session: Session,
    customer_id: str = "",
    reason_code: str = "",
    issue_summary: str = "",
    root_cause: str = "",
    recommended_action: str = "",
    order_id: str | None = None,
    amount_usd: float | None = None,
) -> dict:
    """Never identity-gated. Stores a FAT ticket a human can act on without the
    transcript; returns a LEAN result the agent reads to the customer."""
    if reason_code not in REASON_CODES:
        return fail(
            VALIDATION,
            f"reason_code must be one of {', '.join(REASON_CODES)}; got {reason_code!r}.",
        )

    requested_human = reason_code == "customer_requested_human"
    # A customer asking for a person is honoured immediately, so the bar is lower:
    # a clear summary is enough. Every other ticket must stand on its own.
    min_chars, min_words = (20, 4) if requested_human else (MIN_SUMMARY_CHARS, 6)
    if _too_thin(issue_summary, min_chars, min_words):
        return fail(
            VALIDATION,
            "issue_summary is too thin for a human who cannot see this conversation. "
            "Write 2-3 sentences: who the customer is, which order and item, what "
            "happened, and what they want.",
        )
    if not requested_human:
        missing = [
            name
            for name, value in (
                ("root_cause", root_cause),
                ("recommended_action", recommended_action),
            )
            if _too_thin(value, 15, 3)
        ]
        if missing:
            return fail(
                VALIDATION,
                f"{' and '.join(missing)} missing or too thin. State why you could not "
                "resolve this (cite the rule or failure) and the concrete next step for "
                "the human.",
            )

    order_id = str(order_id).strip().upper() if order_id else None
    if reason_code in ORDER_REASONS:
        if not order_id:
            return fail(VALIDATION, f"order_id is required for reason_code '{reason_code}'.")
        if (
            amount_usd is None
            or isinstance(amount_usd, bool)
            or not isinstance(amount_usd, (int, float))
            or amount_usd <= 0
        ):
            return fail(
                VALIDATION,
                f"amount_usd (a positive number) is required for reason_code '{reason_code}'.",
            )

    # Identity comes from SESSION STATE, never from what the model passed in.
    verified_id = session.verified_customer_id
    customer = next(
        (c for c in backend.CUSTOMERS.values() if c["customer_id"] == verified_id), None
    )
    ticket_customer = {
        "customer_id": verified_id or "UNVERIFIED",
        "identity_verified": verified_id is not None,
        "name": customer["name"] if customer else None,
        "email": customer["email"] if customer else None,
        "tier": customer["tier"] if customer else None,
    }
    if not verified_id and customer_id and customer_id.upper() != "UNVERIFIED":
        ticket_customer["claimed_customer_id"] = customer_id

    # Order facts come from the backend (system of record), and only if the order
    # belongs to the verified customer.
    ticket_order = None
    if order_id:
        order = backend.get_order(order_id)
        if order is not None and verified_id and order["customer_id"] == verified_id:
            ticket_order = {
                "order_id": order["order_id"],
                "item": order["item"],
                "total_usd": order["total"],
                "status": order["status"],
                "days_since_delivery": order["days_since_delivery"],
                "return_window_open": backend.return_window_open(order),
                "refunded_before_this_session_usd": order["already_refunded"],
                "refunded_this_session_usd": session.refunded_by_order.get(order_id, 0.0),
            }
            if order.get("carrier_note"):
                ticket_order["carrier_note"] = order["carrier_note"]
        else:
            ticket_order = {"order_id": order_id, "note": "Not verified against this customer."}

    # Idempotent: a retry of the same escalation returns the same ticket.
    for existing in session.tickets:
        same_order = (existing.get("order") or {}).get("order_id") == order_id
        if existing["reason_code"] == reason_code and same_order:
            return ok(
                ticket_id=existing["ticket_id"],
                queue=existing["queue"],
                response_within_hours=existing["response_within_hours"],
            )

    queue = QUEUE_BY_REASON[reason_code]
    ticket = {
        "ticket_id": session.next_ticket_id(),
        "created_at": session.now,
        "queue": queue,
        "priority": (
            "high" if reason_code in {"suspected_fraud", "over_refund_ceiling"} else "normal"
        ),
        "response_within_hours": ESCALATION_SLA_HOURS,
        "reason_code": reason_code,
        "customer": ticket_customer,
        "order": ticket_order,
        "amount_in_dispute_usd": (
            round(float(amount_usd), 2)
            if isinstance(amount_usd, (int, float)) and not isinstance(amount_usd, bool)
            else None
        ),
        "issue_summary": str(issue_summary).strip(),
        "root_cause": str(root_cause or "").strip() or None,
        "recommended_action": str(recommended_action or "").strip() or None,
        "refunds_issued_this_session": dict(session.refunded_by_order),
    }
    session.tickets.append(ticket)
    session.log(
        event="escalation",
        ticket_id=ticket["ticket_id"],
        reason_code=reason_code,
        customer_id=ticket_customer["customer_id"],
        order_id=order_id,
    )
    # Lean: the agent only needs to tell the customer a ticket number and a time.
    return ok(
        ticket_id=ticket["ticket_id"],
        queue=queue,
        response_within_hours=ESCALATION_SLA_HOURS,
    )


HANDLERS = {
    "get_customer": get_customer,
    "lookup_order": lookup_order,
    "process_refund": process_refund,
    "escalate_to_human": escalate_to_human,
}
