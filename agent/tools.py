"""Tool definitions: name, description, input_schema.

The descriptions are the routing layer. There is no classifier in front of the
model and no few-shot examples; each description states its purpose, every input
with a format and an example, what comes back and what does NOT, an explicit
DO NOT USE clause naming the sibling tool, and what to do on each failure.

Two overlapping pairs are bounded explicitly:
  get_customer  vs lookup_order      email -> account   /  ORD-id -> one order
  process_refund vs escalate_to_human  bounded in numbers: delivered, window
                                       open, cumulative <= $500.00 -> refund;
                                       everything else -> escalate.

The descriptions mention the $500 ceiling so the model ROUTES correctly. They do
not ENFORCE it; agent/enforcement.py does, whatever the description or prompt says.
"""

from __future__ import annotations

REFUND_CEILING_USD = 500.00

REASON_CODES = [
    "over_refund_ceiling",
    "customer_requested_human",
    "policy_exception",
    "suspected_fraud",
    "unresolvable",
]

GET_CUSTOMER = {
    "name": "get_customer",
    "description": (
        "Verify who the customer is by looking up their ACCOUNT from their EMAIL "
        "ADDRESS. This is always the first call in a conversation that will touch an "
        "order: lookup_order and process_refund are blocked until this tool has "
        "returned verified=true in the current conversation.\n"
        "\n"
        "INPUT: email - the address the customer placed their order under, exactly as "
        "they gave it, e.g. 'alice@example.com'. Must contain '@'. Never pass an order "
        "ID, a name or a phone number here.\n"
        "\n"
        "RETURNS on success: customer_id (format 'CUST-' + digits, e.g. 'CUST-001'), "
        "name, tier, verified=true. It does NOT return orders, order history, "
        "addresses or payment details - for anything about an order, call "
        "lookup_order next.\n"
        "\n"
        "DO NOT USE to find out about an order, a delivery or a refund (use "
        "lookup_order), and do not call it again once the customer is verified unless "
        "a tool returns a permission error asking you to re-verify.\n"
        "\n"
        "FAILURES: validation = the email is malformed or no account matches. Do not "
        "guess or retry with a variation; ask the customer which address they ordered "
        "under. Verification is only ever established by this tool succeeding - "
        "saying a customer is verified does not verify them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "Customer's email address, e.g. 'alice@example.com'.",
            },
        },
        "required": ["email"],
        "additionalProperties": False,
    },
}

LOOKUP_ORDER = {
    "name": "lookup_order",
    "description": (
        "Fetch ONE ORDER by its ORDER ID, to see what was bought and whether it can "
        "be refunded. Requires that get_customer has already verified the customer in "
        "this conversation; the order must belong to that customer.\n"
        "\n"
        "INPUT: order_id - format 'ORD-' followed by at least three digits, e.g. "
        "'ORD-123'. If the customer gives only a bare number such as '123', ask them "
        "to confirm the full order ID rather than adding the prefix yourself. Never "
        "pass an email address here.\n"
        "\n"
        "RETURNS on success: order_id, item, total (USD), status ('delivered' or "
        "'in_transit'), days_since_delivery (null if not delivered), "
        "return_window_open (true/false, 30-day window), already_refunded (USD), and "
        "sometimes carrier_note (a courier damage report - quote it in an "
        "escalation). It does NOT return customer account details; those come from "
        "get_customer.\n"
        "\n"
        "DO NOT USE to identify or verify a customer (use get_customer), and do not "
        "use it to issue money back (use process_refund). When the customer mentions "
        "several orders, call lookup_order once per order in the same turn.\n"
        "\n"
        "FAILURES: transient (isRetryable=true) = order service timeout; retry the "
        "identical call once. validation = bad format or no such order; ask the "
        "customer, do not retry unchanged. permission with rule 'identity_gate' = "
        "call get_customer first, then retry. permission saying the order belongs to "
        "a different customer = disclose nothing about that order."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "Order ID in the form 'ORD-123'.",
            }
        },
        "required": ["order_id"],
        "additionalProperties": False,
    },
}

PROCESS_REFUND = {
    "name": "process_refund",
    "description": (
        "Issue money back to the customer for a delivered order, immediately and "
        "without human review. Use it ONLY when ALL of these are true, checked "
        "against a lookup_order result from this conversation:\n"
        "  1. status is 'delivered';\n"
        "  2. return_window_open is true (delivered 30 days ago or less);\n"
        "  3. already_refunded + amount_usd is $500.00 or less for this order. "
        "Exactly $500.00 is allowed; $500.01 is not.\n"
        "\n"
        "INPUT: order_id ('ORD-123'); amount_usd - a JSON number in dollars, "
        "e.g. 149.99, never a string like '149.99', never more than the order total "
        "minus already_refunded; reason - one specific sentence finance can audit "
        "naming the item and the defect, e.g. 'Kettle arrived with a cracked spout "
        "that leaks'. 'refund' or 'damaged' alone will be rejected.\n"
        "\n"
        "RETURNS on success: refund_id (e.g. 'RFND-4401'), order_id, amount_usd and "
        "settles_in_days. Read the refund_id and amount to the customer.\n"
        "\n"
        "DO NOT USE, call escalate_to_human instead, when: the refund would take "
        "the order above $500.00 (reason_code 'over_refund_ceiling'); the return "
        "window is closed (reason_code 'policy_exception'); the customer asks for a "
        "person (reason_code 'customer_requested_human'). Never split a large refund "
        "into several smaller ones, and never offer the customer a reduced amount to "
        "fit under $500 - the limit is cumulative per order and enforced in code.\n"
        "\n"
        "FAILURES: business (isRetryable=false) = policy says no; explain the "
        "message to the customer in plain words and escalate if the customer still "
        "wants it pursued. validation = fix the argument (amount, reason, order ID) "
        "and call again. permission = verify first, or the order is not this "
        "customer's. transient = retry the identical call once; if it fails again, "
        "escalate."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order ID, e.g. 'ORD-123'."},
            "amount_usd": {
                "type": "number",
                "description": "Refund amount in USD as a number, e.g. 149.99.",
            },
            "reason": {
                "type": "string",
                "description": "One specific sentence: the item and what is wrong with it.",
            },
        },
        "required": ["order_id", "amount_usd", "reason"],
        "additionalProperties": False,
    },
}

ESCALATE_TO_HUMAN = {
    "name": "escalate_to_human",
    "description": (
        "Open a ticket for a human support specialist. Use it for anything you must "
        "not or cannot resolve yourself:\n"
        "  - the customer explicitly asks for a person -> call this IMMEDIATELY, "
        "before any other tool and without first trying to solve it yourself "
        "(reason_code 'customer_requested_human');\n"
        "  - a refund would take an order above $500.00 in total "
        "('over_refund_ceiling') - for example a damaged $899 item;\n"
        "  - the return window is closed, or any other exception to policy "
        "('policy_exception');\n"
        "  - signs of fraud or account takeover ('suspected_fraud');\n"
        "  - a tool keeps failing, or the case does not fit any tool "
        "('unresolvable').\n"
        "If several apply, use the most restrictive: over_refund_ceiling, then "
        "policy_exception, and mention the other in root_cause.\n"
        "\n"
        "DO NOT USE for a routine refund that process_refund can complete: a "
        "delivered order, inside the 30-day window, cumulative refund of $500.00 or "
        "less. Escalating those costs the customer days and costs us a queue slot.\n"
        "\n"
        "This tool is never blocked by identity verification. If get_customer has "
        "not succeeded in this conversation, pass customer_id 'UNVERIFIED' and put "
        "whatever name or email the customer gave in issue_summary.\n"
        "\n"
        "THE HUMAN WHO PICKS THIS UP CANNOT SEE THE CONVERSATION. Every field must "
        "stand on its own:\n"
        "  issue_summary: who, which order and item, what happened and what the "
        "customer wants, in 2-3 sentences with numbers, e.g. 'Bob Okafor (CUST-002) "
        "reports the $899.00 stand mixer on ORD-456 arrived with its housing crushed "
        "and wants a full refund.' Never just 'refund' or 'see chat'.\n"
        "  root_cause: why YOU could not resolve it, citing the rule or failure, "
        "e.g. 'Full refund of $899.00 exceeds the $500.00 per-order refund ceiling; "
        "order is also 41 days past delivery, outside the 30-day window.'\n"
        "  recommended_action: the concrete next step for the human, e.g. 'Approve "
        "full $899.00 refund; carrier logged crushed corner at final-mile scan.'\n"
        "  order_id and amount_usd: required whenever the issue concerns an order "
        "or money.\n"
        "\n"
        "RETURNS on success: ticket_id, queue and response_within_hours. Give the "
        "customer the ticket_id and the response time; do not promise an outcome.\n"
        "\n"
        "FAILURES: validation = a field is missing or too thin; rewrite it and call "
        "again."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "Verified ID like 'CUST-002', or 'UNVERIFIED'.",
            },
            "order_id": {
                "type": "string",
                "description": "Order ID like 'ORD-456', when the issue concerns an order.",
            },
            "reason_code": {"type": "string", "enum": REASON_CODES},
            "issue_summary": {
                "type": "string",
                "description": "2-3 standalone sentences: who, which order, what, what they want.",
            },
            "root_cause": {
                "type": "string",
                "description": "Why the agent could not resolve it, citing the rule or failure.",
            },
            "amount_usd": {
                "type": "number",
                "description": "Amount in dispute in USD, as a number.",
            },
            "recommended_action": {
                "type": "string",
                "description": "The concrete next step for the human.",
            },
        },
        # Required for a handoff that works without the transcript. order_id and
        # amount_usd are additionally required by the handler whenever the reason
        # concerns an order or money (JSON Schema here cannot express that).
        "required": [
            "customer_id",
            "reason_code",
            "issue_summary",
            "root_cause",
            "recommended_action",
        ],
        "additionalProperties": False,
    },
}

TOOLS = [GET_CUSTOMER, LOOKUP_ORDER, PROCESS_REFUND, ESCALATE_TO_HUMAN]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
