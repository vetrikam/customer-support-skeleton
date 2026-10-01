# customer-support-skeleton

Starter repository for **Claude Architect, Week 1, Assignment 1: Multi-Tool Agent with
Escalation Logic**.

You are building a customer support resolution agent for a mid-size e-commerce retailer.
It handles returns, billing disputes and account issues, and reaches the backend through
four tools. The business target is 80%+ first-contact resolution, with correct escalation
on the remainder, and three non-negotiables:

- No order operation may occur before customer identity is verified.
- Refunds above $500 require human approval.
- Explicit customer requests for a human are honoured immediately.

---

## Get it running first

```bash
python -m venv .venv
. .venv/Scripts/activate        # Windows.  macOS/Linux: . .venv/bin/activate
pip install -r requirements.txt

python run.py --demo            # no API key needed
```

You should see a scripted run that verifies Alice, looks up ORD-123, and terminates on
`end_turn`. That is the wiring check: two handlers work, the identity gate lets them
through, the loop turns over. Everything else is yours.

Then add your key and try the real thing:

```bash
cp .env.example .env            # put your ANTHROPIC_API_KEY in it
python run.py --live --scenario happy
```

It will not get far yet. `process_refund` raises `NotImplementedError`. That is Task 3.

---

## What is here

| File | State |
|---|---|
| `agent/backend.py` | Given. Fixtures: customers, orders, the return window |
| `agent/errors.py` | Given. The result envelope and the four error categories |
| `agent/model.py` | Given. LiveModel and ScriptedModel behind one interface |
| `agent/transcript.py` | Given. Markdown transcript recorder |
| `agent/session.py` | Done. Per-order refund tracking the ceiling reads |
| `agent/handlers.py` | Done. All four handlers, plus the FAIL_MODE switch |
| `agent/enforcement.py` | Done. Identity gate and cumulative $500 ceiling |
| `agent/loop.py` | Done. Full stop_reason handling, labelled safety net |
| `agent/tools.py` | Done. Full descriptions with explicit boundaries |
| `tests/test_enforcement.py` | Done. 26 tests including the hostile-prompt run |

Every gap is marked `TODO (Task N)` and names the task below. Search for `TODO` to find
all of them.

---

## The six tasks

**1. Tool descriptions** (`agent/tools.py`). Each description must state purpose, expected
inputs with formats, outputs, and explicit boundaries against similar tools. At least two
tools have genuinely overlapping surface area. Write descriptions that let the model
discriminate between them reliably despite that overlap. Fix it in the descriptions, not
with a routing layer in front of the model.

**2. The agentic loop** (`agent/loop.py`). Correct `stop_reason` handling: continue on
`tool_use`, terminate on `end_turn`, append tool results to conversation history between
iterations. You must not use natural-language parsing or an iteration cap as your primary
termination mechanism. A cap as a safety net is fine and expected: label it as such in a
comment. Three gaps are marked 2A, 2B and 2C.

**3. Structured errors** (`agent/handlers.py`). Every tool returns structured errors with
`errorCategory` (transient / validation / business / permission), `isRetryable`, and a
human-readable description. Business rule violations are not retryable and carry a
customer-facing explanation. The helpers are in `errors.py`; the two worked handlers show
the pattern. Demonstrate the agent behaving correctly against each category.

**4. Programmatic enforcement** (`agent/enforcement.py`). Two enforcement points, both in
code, neither in the system prompt:

- a prerequisite gate blocking `lookup_order` and `process_refund` until `get_customer`
  returns a verified customer ID (**built for you, read it**);
- a tool-call interception hook blocking refunds above $500 (**yours**).

Prove the enforcement holds with a test where the system prompt is deliberately weakened
or instructed to skip verification, and show the gate still blocks. That test carries
significant weight in the rubric. Scaffolding is in `tests/test_enforcement.py`.

**5. Multi-concern handling.** A single message containing two or more distinct issues:
decompose them, investigate in parallel against shared context, answer once. This mostly
falls out of Task 2B, and it will not work until you fix it.

**6. Structured handoff** (`agent/handlers.py`). Escalation produces a summary containing
customer ID, root cause, amounts and recommended action. Assume the human has no access to
the transcript.

---

## Fixtures

| Order | Customer | Amount | State |
|---|---|---|---|
| ORD-123 | Alice Nguyen, CUST-001 | $149.99 | Delivered 6 days ago, return window open |
| ORD-456 | Bob Okafor, CUST-002 | $899.00 | Delivered 41 days ago, housing crushed. Over the ceiling **and** outside the window |
| ORD-321 | Bob Okafor, CUST-002 | $64.00 | Delivered 9 days ago, refundable |
| ORD-789 | Alice Nguyen, CUST-001 | $28.00 | Still in transit. First lookup fails transiently |

Emails are `alice@example.com` and `bob@example.com`. These are the same records used in
the Week 1 laboratory notebook, so anything demoed live behaves identically here.

---

## Deliverables

1. **This repository, extended and runnable.**
2. **Two transcripts**, in `transcripts/`:
   - happy path: Alice, ORD-123
   - escalation: Bob, ORD-456, or an explicit "I want a human"

   Generate them with `python run.py --live --scenario happy` and
   `python run.py --live --scenario escalation`. `--scenario human` gives you the explicit
   request for a person, if you would rather use that one.
3. **This README, with the three reflection answers filled in below.**

---

## What I built

| Task | Where | What |
|---|---|---|
| 1. Tool descriptions | `agent/tools.py` | Purpose, inputs with formats and examples, what comes back and what does not, a DO NOT USE clause naming the sibling tool, and what to do on each failure. Both overlapping pairs are bounded explicitly, the refund/escalate pair in numbers. |
| 2. Loop | `agent/loop.py` | `stop_reason` drives everything: `tool_use` executes **every** block and appends all results in one user message; `end_turn`/`stop_sequence` return; `pause_turn` resumes; `refusal` returns flagged; `max_tokens` and unknown reasons raise `LoopError`. `MAX_TURNS` is labelled as a safety net and raises if reached. |
| 3. Structured errors | `agent/handlers.py` | `process_refund` returns validation (bad ID, bad amount, thin reason), business (not delivered, window closed, more than is owed) or a lean success. Nothing raises into the loop. |
| 4. Enforcement | `agent/enforcement.py`, `agent/session.py` | Identity gate (skeleton) plus the $500 ceiling: amount type check, order ownership, and a **cumulative per-order** total read from session state and the backend, never the conversation. |
| 5. Multi-concern | `agent/loop.py` | Falls out of 2B: Bob's two orders are looked up in one turn and answered in one reply. |
| 6. Handoff | `agent/handlers.py` | `escalate_to_human` is never identity-gated. It stores a fat ticket (verified identity from session, order facts from the backend, amount, root cause, recommended action, refunds already issued) and returns only `ticket_id`, `queue`, `response_within_hours`. Stub summaries are rejected; retries are idempotent. |

### Run it

```bash
python run.py --demo                                   # wiring check, no key
python run.py --live --scenario happy                  # Alice / ORD-123
python run.py --live --scenario escalation             # Bob / ORD-456 + ORD-321
python run.py --live --scenario human                  # "I want a person"
python -m unittest discover -s tests -t .              # 26 tests, no key
```

### Error categories (FAIL_MODE)

`FAIL_MODE` injects one error category into the first `process_refund` call of a run, so
the agent's different reactions are visible in a live transcript:

```bash
FAIL_MODE=transient  python run.py --live --scenario happy   # retries the same call
FAIL_MODE=validation python run.py --live --scenario happy   # rewrites the reason, calls again
FAIL_MODE=business   python run.py --live --scenario happy   # explains, escalates, no retry
FAIL_MODE=permission python run.py --live --scenario happy   # re-verifies, then retries
```

Transcripts land in `transcripts/` as `happy-fail-<mode>.md`. Separately, `ORD-789` fails
its first lookup with a real transient error in every run.

---

## Reflection

### 1. You put the refund ceiling in code. Argue the case for a system prompt instruction instead, then explain why it loses. Be specific about failure rate and money.

**The case for the prompt.** A prompt instruction is judgement, and a hook is a blunt
number. The model can read context a hook cannot: a gold-tier customer, a carrier note
confirming the damage, a $510 claim where the $10 is shipping. It can explain the limit in
the customer's terms before they ask, instead of hitting a wall mid-flow. It can be
changed in minutes without a deploy, A/B tested, and localised. And on a clean, honest
conversation with a capable model, it will follow "escalate anything above $500" nearly
every time. If compliance were 100%, the prompt would be strictly better: same outcome,
better conversation.

**Why it loses.** A prompt instruction is a probability; a gate is a guarantee, and the
money is entirely in the gap.

*Assumptions (illustrative, not real data, for a mid-size retailer): 40,000 conversations
a month, 5% with claims above $500, average over-ceiling claim $850, and a quarter of
unapproved over-ceiling refunds being genuine losses.*

- 40,000 support conversations a month; 5% involve a claim above $500 → **2,000/month**.
- Prompt compliance of 99% on ordinary traffic is optimistic once you include long
  conversations, model upgrades, prompt edits by other teams and adversarial customers
  ("my manager already approved this", "just do it in two parts"). Even at 99%, that is
  **20 unapproved over-ceiling refunds a month**; at 97%, 60.
- Average over-ceiling claim ≈ $850. Not every slip is a loss (many were legitimate), but
  the control exists because perhaps a quarter are not: fraud, wrong amount, duplicate.
  20 × 12 × $850 × 25% ≈ **$51,000/year** at 99%; ≈ $150,000/year at 97%. The exposure is
  also unbounded per incident: nothing in a prompt stops a $5,000 refund.
- The cumulative case is worse. "Refund $500, then $399" requires the model to remember
  and add up its own earlier tool results; after context compaction it may not have them.
  My adversarial test does exactly this split, and the gate blocks it every time because
  it reads `session.refunded_by_order`, not the transcript.
- Evidence: `tests/test_enforcement.py::AdversarialPrompt` runs the whole agent under a
  system prompt ordering it to skip verification, ignore limits and split refunds. The
  audit log shows the first call blocked by `identity_gate`, three `refund_ceiling`
  blocks ($899, $399, $200), the other customer's order blocked by `order_ownership`, and
  the total refunded capped at $500.00.

**What the gate costs instead:** about 40 lines and a test file, zero per-conversation
cost, and a small amount of over-escalation at the boundary, which a human clears in
minutes. It is also auditable: every block is a `rule="refund_ceiling"` entry an auditor
can count. "The prompt says so" is not a control a finance team can sign off.

**The same lesson from my own work.** On a global logistics company's marketing site,
published in 234 languages on AEM, legal and brand approval originally depended on
process: authors and agencies emailed a preview link to reviewers and legal, then waited.
Nothing in the platform stopped publishing before approval arrived, so compliance relied
on people remembering, and the email loop delayed approvals and slipped several client
deliverables. We replaced it with an AEM workflow that blocked publish until legal
approved. Unapproved publishes dropped to zero, and the workflow gave us something the
email process never had: every approval was tracked, content nearing expiry was flagged,
and legal could prioritise what was about to lapse. That maps directly onto this agent.
The system prompt is the email asking people to get approval; the enforcement hook is the
workflow that makes publish impossible without it. And like the AEM workflow, the gate's
second benefit is visibility: every `refund_ceiling` block is an audit entry an operations
team can count and act on, which a prompt instruction never produces.

The descriptions still mention $500, because the model should *route* correctly and tell
the customer up front. That is user experience. The guarantee lives in code.

### 2. Your two similar tools: what did you write so Claude can tell them apart, and how would you notice misrouting in production?

**`process_refund` vs `escalate_to_human`.** I bounded them in numbers, not adjectives.
`process_refund` opens with: *"Use it ONLY when ALL of these are true ... status is
'delivered'; return_window_open is true (delivered 30 days ago or less);
already_refunded + amount_usd is $500.00 or less for this order. Exactly $500.00 is
allowed; $500.01 is not."* Its DO NOT USE clause names the sibling and the reason code
for each case: *"call escalate_to_human instead, when: the refund would take the order
above $500.00 (reason_code 'over_refund_ceiling'); the return window is closed
(reason_code 'policy_exception'); the customer asks for a person (reason_code
'customer_requested_human')."* The escalation tool carries the
mirror image: *"DO NOT USE for a routine refund that process_refund can complete: a
delivered order, inside the 30-day window, cumulative refund of $500.00 or less.
Escalating those costs the customer days and costs us a queue slot."* It also gives a
tie-break when several reasons apply.

**`get_customer` vs `lookup_order`.** I keyed them to their input: *"look up their
ACCOUNT from their EMAIL ADDRESS"* versus *"Fetch ONE ORDER by its ORDER ID"*, each with a
format and example, an explicit *"Never pass an order ID ... here"* / *"Never pass an email address here"*, and a statement
of what each does **not** return ("It does NOT return orders..." / "It does NOT return
customer account details").

**Detecting misroutes.** The two directions look different in production:

- *Refund attempted when it should have escalated* is **loud**. The gate blocks it and
  logs `rule="refund_ceiling"`. Dashboard: ceiling blocks per 1,000 conversations. A
  rise after a prompt or model change means routing degraded, even though no money left.
  Alert on any refund event above $500 at all (should be impossible) and on repeat blocks
  in one session (a model trying to split).
- *Escalated when it should have refunded* is **invisible**. The ticket looks perfectly
  reasonable and the customer just waits. Detect it from the tickets themselves: share of
  `over_refund_ceiling` tickets with `amount_in_dispute_usd <= 500`; share of
  `policy_exception` tickets where `return_window_open` is true; escalation rate overall
  and first-contact resolution against the 80% target. A weekly sample of escalations
  that a human closed with "just refunded it, nothing special" is the ground truth.
- For the identity pair: `lookup_order` validation errors whose input contains `@`, and
  `get_customer` errors whose input matches `ORD-`; identity-gate blocks per conversation.

In my live escalation transcript (`transcripts/escalation.md`), Claude routed both
directions correctly in a single message: it refunded the $64.00 skillet on ORD-321
directly and escalated the $899.00 mixer on ORD-456 as `over_refund_ceiling`, citing the
closed return window in `root_cause`. That one run is the routing boundary working in
both directions.

### 3. Every `tool_result` is resent on later turns. What did you strip from payloads, and what would break if a summary dropped the refund amount?

**What I stripped.** Each field is paid for on every remaining turn, so results carry only
what the agent reads next:

- `get_customer`: no echo of the email, no address, payment methods or order history —
  `customer_id`, `name`, `tier`, `verified`.
- `process_refund`: `refund_id`, `order_id`, `amount_usd`, `settles_in_days`. No echo of
  the reason, no processor transaction ID, timestamps, running totals or audit metadata —
  those go to `session.audit`.
- `escalate_to_human`: only `ticket_id`, `queue`, `response_within_hours`. The ticket
  itself is fat (customer, order facts, carrier note, amounts, root cause, recommended
  action, refunds already issued) but it goes to the human, not back into the context.
  Echoing it would cost ~20× the tokens on every later turn and add nothing the agent says.
- Error envelopes carry the category, retryability and one actionable message — no stack
  traces.

**If a summary dropped the refund amount.** Trace it: the agent no longer knows $149.99
went back on ORD-123.

- *Customer communication breaks.* It may quote a wrong or vague amount, or tell the
  customer nothing has been refunded yet.
- *It may try again.* A second `process_refund` would be rejected by the handler's
  "more than is still owed" check, because that reads `session.refunded_by_order` — a
  business error, not a double payment.
- *Escalation tickets would be thinner where the model writes them*, but the
  `refunds_issued_this_session` and `refunded_this_session_usd` fields come from session
  state, so the human still sees the truth.
- *The ceiling still holds.* The number it depends on lives in `session.refunded_by_order`
  (and the order's `already_refunded` in the backend), written only by a successful
  `process_refund`. The transcript is a view of that state, not the state. That is the
  design point of the whole assignment: anything that must be true belongs in state the
  model cannot edit or forget, and the context window is allowed to be lossy.

---

## Rubric

| Criterion | Points |
|---|---|
| Tool descriptions | 15 |
| Agentic loop | 20 |
| Error handling | 15 |
| Enforcement | 25 |
| Multi-concern and handoff | 15 |
| Reflection | 10 |

Weight: 20% of the course. Estimated 2 hours. Maps to exam Scenario 1, Customer Support
Resolution Agent, Domains 1 (primary), 2 and 5.

---

## Licence

MIT. See [LICENSE](LICENSE). Fork it, change it, keep your work.
