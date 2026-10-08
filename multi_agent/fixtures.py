"""Assignment 2 fixtures: the ticket, its four concerns, and the charge history.

CONCERNS is the ground truth for the coverage check. It is written from the ticket
text, not from any coordinator's output, so the check compares the coordinator
against the ticket and not against itself.
"""

from __future__ import annotations

TICKET = (
    "Hi, it's Alice Nguyen (alice@example.com). Three things. The pour-over kettle from "
    "ORD-123 arrived with a crack in the spout, please refund it. I was charged twice "
    "for the espresso tamper on ORD-789, can you look into that? And if I wait until "
    "ORD-789 arrives, can I still return it, and how long do I have? If the double "
    "charge isn't sorted by Friday I'll dispute it with my bank as a chargeback."
)

CONCERNS = [
    {"id": "C1", "kind": "refund", "summary": "Cracked kettle on ORD-123: refund"},
    {"id": "C2", "kind": "investigate", "summary": "Possible duplicate charge on ORD-789: investigate"},
    {"id": "C3", "kind": "policy", "summary": "Return window for ORD-789 after arrival: policy read"},
    {"id": "C4", "kind": "escalate", "summary": "Chargeback threat if not sorted by Friday: escalate"},
]

# Charge history per order. An order with no entry has no charges: a VALID EMPTY result,
# which is different from an access failure (see tools.find_charges).
CHARGES = {
    "ORD-789": [
        {"charge_id": "ch_8f21", "amount_usd": 28.00, "date": "2026-10-01"},
        {"charge_id": "ch_8f77", "amount_usd": 28.00, "date": "2026-10-01"},
    ],
}
