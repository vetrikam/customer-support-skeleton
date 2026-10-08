"""Tool implementations behind the specialists.

Reuses Assignment 1's backend and result envelope, so both assignments share one set
of records and one error contract. The refund CEILING is not here: it is a PreToolUse
hook in hooks.py, so it applies to every agent that can call process_refund.
"""

from __future__ import annotations

from agent.backend import get_order
from agent.errors import BUSINESS, PERMISSION, TRANSIENT, VALIDATION, fail, ok

from .fixtures import CHARGES


def _order(args):
    order = get_order(str(args.get("order_id", "")))
    if order is None:
        return None, fail(VALIDATION, "No order with that ID. Order IDs look like ORD-123.")
    return order, None


def _not_on_account():
    return fail(PERMISSION, "That order is not on this customer's account, so it cannot be accessed here.")


def lookup_order(args, ledger):
    order, err = _order(args)
    if err:
        return err
    return ok(
        order_id=order["order_id"],
        customer_id=order["customer_id"],
        item=order["item"],
        status=order["status"],
        total=order["total"],
        days_since_delivery=order["days_since_delivery"],
        already_refunded=order["already_refunded"],
    )


def find_charges(args, ledger):
    order, err = _order(args)
    if err:
        return err
    if args.get("customer_id") != order["customer_id"]:
        return _not_on_account()
    if ledger.charge_failures_left > 0:
        ledger.charge_failures_left -= 1
        return fail(TRANSIENT, "The payment service timed out. Retrying the same call may succeed.")
    # An empty list is a valid result: the lookup worked and there is nothing to report.
    return ok(order_id=order["order_id"], charges=CHARGES.get(order["order_id"], []))


def read_policy(args, ledger):
    from agent.backend import RETURN_WINDOW_DAYS

    return ok(return_window_days=RETURN_WINDOW_DAYS, window_starts="delivery", source="policy:returns#window")


def process_refund(args, ledger):
    order, err = _order(args)
    if err:
        return err
    oid = order["order_id"]
    amount = args.get("amount_usd")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        return fail(VALIDATION, "amount_usd must be a positive number, e.g. 149.99.")
    if not str(args.get("reason", "")).strip():
        return fail(VALIDATION, "reason is required, e.g. 'cracked on arrival'.")
    if args.get("customer_id") != order["customer_id"]:
        return _not_on_account()
    if order["status"] != "delivered":
        return fail(BUSINESS, f"{oid} has not been delivered yet, so it cannot be refunded.")
    refunded = ledger.refunded_by_order.get(oid, order["already_refunded"])
    owed = round(order["total"] - refunded, 2)
    if round(amount, 2) > owed:
        return fail(BUSINESS, f"That is more than is still owed on {oid} (${owed:.2f}).")
    ledger.refunded_by_order[oid] = round(refunded + amount, 2)
    refund = {
        "refund_id": f"RF-{len(ledger.refunds) + 1:03d}",
        "order_id": oid,
        "amount_usd": round(amount, 2),
        "reason": args["reason"],
    }
    ledger.refunds.append(refund)
    return ok(refund_id=refund["refund_id"], order_id=oid, amount_usd=refund["amount_usd"], settles_in_days=5)


REQUIRED_TICKET_FIELDS = ("customer_id", "order_id", "root_cause", "recommended_action")


def escalate_ticket(args, ledger):
    for name in REQUIRED_TICKET_FIELDS:
        if not str(args.get(name, "")).strip():
            return fail(VALIDATION, f"{name} is required so a human can act without the transcript.")
    ticket = dict(args)
    ticket.update(
        ticket_id=f"TKT-{len(ledger.tickets) + 1:04d}",
        queue="billing-escalations",
        response_within_hours=24,
    )
    ledger.tickets.append(ticket)
    return ok(ticket_id=ticket["ticket_id"], queue=ticket["queue"], response_within_hours=24)


TOOL_FUNCS = {
    "lookup_order": lookup_order,
    "find_charges": find_charges,
    "read_policy": read_policy,
    "process_refund": process_refund,
    "escalate_ticket": escalate_ticket,
}
ALL_TOOL_NAMES = list(TOOL_FUNCS)
