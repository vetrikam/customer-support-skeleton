#!/usr/bin/env python3
"""Single-agent baseline for the Assignment 2 token comparison.

Same five tools, same refund hook, same ticket, same simulated payments outage. One agent does
everything itself, with no subagents. Compare its cost and tokens with the multi-agent run.

    python run_single.py --scenario ticket --compare transcripts/live-ticket.md
Writes transcripts/single-ticket.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query,
)

from agent.model import MODEL_ID
from multi_agent.hooks import build_hooks
from multi_agent.ledger import Ledger
from run_live import MCP_SERVER, PREFIX, SCENARIOS, TOOL_FUNCS, load_dotenv, make_server

ROOT = pathlib.Path(__file__).parent
PROMPT = (ROOT / "multi_agent" / "prompts" / "single_agent.md").read_text(encoding="utf-8")


def build_options(ledger: Ledger) -> ClaudeAgentOptions:
    server = make_server(ledger)
    return ClaudeAgentOptions(
        system_prompt=PROMPT,
        mcp_servers={MCP_SERVER: server},
        allowed_tools=[PREFIX + t for t in TOOL_FUNCS],
        tools=[],  # no built-in tools: only the five support tools
        hooks=build_hooks(ledger, matcher=PREFIX + "process_refund"),  # the same ceiling hook as the multi-agent run
        max_turns=40,
        model=MODEL_ID,
    )


def token_totals(usage: dict) -> dict:
    fresh = usage.get("input_tokens", 0)
    created = usage.get("cache_creation_input_tokens", 0)
    read = usage.get("cache_read_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    return {"fresh_input": fresh, "cache_written": created, "cache_read": read, "output": out,
            "total": fresh + created + read + out}


def read_run(path: pathlib.Path) -> dict | None:
    """Pull cost and usage back out of a saved transcript's Usage section."""
    if not path.exists():
        return None
    cost, usage = None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("turns:"):
            cost = float(line.split("cost_usd:")[1].strip())
        if line.startswith("usage: "):
            usage = json.loads(line[len("usage: "):])
    return {"cost": cost, "tokens": token_totals(usage) if usage else None} if cost is not None else None


async def run(scenario: str, dry_run: bool, compare: str | None) -> int:
    ledger = Ledger(charge_failures_left=2 if scenario == "ticket" else 0)
    options = build_options(ledger)
    if dry_run:
        print("Dry run: single-agent options built, no API call.")
        print("tools:", options.allowed_tools, "| subagents:", options.agents, "| built-ins:", options.tools)
        return 0

    lines = [f"# Single-agent baseline: {scenario}", f"Model: {MODEL_ID}", "", "Customer message:", f"> {SCENARIOS[scenario]}", ""]
    result = None
    async for msg in query(prompt=SCENARIOS[scenario], options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    lines.append(f"**assistant:** {block.text}")
                elif isinstance(block, ToolUseBlock):
                    lines.append(f"**tool call:** `{block.name}` {json.dumps(block.input)}")
        elif isinstance(msg, ResultMessage):
            result = msg

    lines += ["", "## Ledger after the run", "```text",
              f"refunds: {ledger.refunds or 'none'}",
              f"hook denials: {[d['reason'] for d in ledger.denials] or 'none'}",
              f"tickets: {[t['ticket_id'] for t in ledger.tickets] or 'none'}", "```"]
    if result is not None:
        lines += ["", "## Usage", "```text",
                  f"turns: {result.num_turns}  is_error: {result.is_error}  cost_usd: {result.total_cost_usd}",
                  f"usage: {json.dumps(result.usage, default=str)}", "```"]
    out = ROOT / "transcripts" / f"single-{scenario}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[single {scenario}] refunds={len(ledger.refunds)} denials={len(ledger.denials)} "
          f"tickets={len(ledger.tickets)} cost_usd={getattr(result, 'total_cost_usd', None)}")
    print(f"wrote {out}")

    if compare:
        single = read_run(out)
        multi = read_run(ROOT / compare)
        if single and multi and single["tokens"] and multi["tokens"]:
            print("\n                 single-agent    multi-agent     multi / single")
            for key in ("fresh_input", "cache_written", "cache_read", "output", "total"):
                s, m = single["tokens"][key], multi["tokens"][key]
                ratio = f"{m / s:.2f}x" if s else "n/a"
                print(f"{key:<16} {s:>12,}  {m:>12,}   {ratio:>10}")
            print(f"{'cost_usd':<16} {single['cost']:>12.4f}  {multi['cost']:>12.4f}   {multi['cost'] / single['cost']:>9.2f}x")
        else:
            print("compare: could not read usage from one of the transcripts")
    return 1 if result is None or result.is_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="ticket")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--compare", help="multi-agent transcript to compare against, e.g. transcripts/live-ticket.md")
    args = parser.parse_args()
    load_dotenv()
    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error("ANTHROPIC_API_KEY is not set. Add it to .env or export it first.")
    return asyncio.run(run(args.scenario, args.dry_run, args.compare))


if __name__ == "__main__":
    raise SystemExit(main())
