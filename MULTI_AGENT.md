# Assignment 2: Multi-Agent Support Ticket Coordinator

A coordinator that splits one four-concern ticket across four specialist subagents, checks coverage against the ticket, and reports each concern as handled, escalated, or unresolved. Built in `multi_agent/`, with runners `run_multi.py` (offline), `run_live.py` (live model), and `run_single.py` (single-agent baseline). Assignment 1 (`agent/`) is unchanged.

## Run it

```bash
pip install -r requirements.txt                          # adds claude-agent-sdk
python run_multi.py                                      # offline, no API key; writes transcripts/multi-agent.md
python run_live.py --dry-run --scenario ticket           # wiring check, no API call
python run_live.py --scenario ticket                     # live: four concerns
python run_live.py --scenario ceiling                    # live: $899 refund, hook denies it
python run_single.py --scenario ticket --compare transcripts/live-ticket.md   # single-agent baseline
python -m unittest discover -s tests -t .                # 39 tests, offline
```

## What is built

| Requirement | Where | What to look at |
|---|---|---|
| Tool scoping | `specs.py`, `run_live.py` | Four `AgentDefinition`s, each with an explicit `tools` list. `unscoped()` omits the field; the offline demo shows the subagent then refunds. |
| Context passing | `specialists.py`, `coordinator.py` | `build_prompt` is the subagent's whole context. The offline demo shows the hedged answer without the order status, then the definitive one with it. |
| Output contract | `results.py` | `Finding` requires concern id, claim, evidence and source. Tests check that every source reaches the report. |
| Hook | `hooks.py`, `run_live.py` | One `PreToolUse` hook on the refund tool for all agents. A denial returns to the agent as a business error with a reason. The refund log stays empty. |
| Failure propagation | `specialists.py`, `coordinator.py` | Offline: a transient payments failure, retried once, then reported with error category, retryability, attempted calls, partial results and alternatives. C2 is named as unresolved; the others still complete. An access failure is kept separate from a valid empty result. |
| Coverage check | `coordinator.py`, `prompts/` | Concerns come from the ticket (`fixtures.py`). Round 1 with the "before" prompt leaves C2 and C4 uncovered; round 2 re-delegates them with the "after" prompt. The diff is in the offline transcript. |

## Live runs

- **Ticket:** the refund processor looks up the order and refunds $149.99 once. The investigation (C2) finishes before the chargeback escalation (C4), which is filed under C4 at $28.00.
- **Ceiling:** the $899 refund is denied by the hook. The denial reaches the agent as data, the ledger shows `refunds: none`, and the escalation writer files one ticket under C1.
- **Single-agent baseline:** same tools, same hook, same ticket. It costs about a third as much and merges the duplicate charge and chargeback into one escalation under C2.

## Design constraints

- **The coordinator has no tools.** It can only delegate (`Agent`, `Task`). A `PreToolUse` hook denies any MCP tool call that doesn't come from a subagent, which is how the rule is enforced in code.
- **Subagents reach the tools by server name.** Each specialist names the `support` server, and only lists the tools its role needs.
- **Every call is traced with the agent that made it.** The trace in each live transcript shows which subagent made each call.

## Known limitations

- The refund processor takes `customer_id` from the order record and doesn't check that the person asking owns the order. Identity checking belongs to Assignment 1 and isn't repeated here.
- The escalation tool only creates tickets. It can't update or relabel one, so a wrong concern ID has to be corrected by a person.
- Token totals in the transcript's usage section may leave out subagent usage. Use the cost figure for comparisons.
- Each comparison is one run per approach. Model output varies, so the cost figures are a single data point.
