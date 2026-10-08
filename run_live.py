#!/usr/bin/env python3
"""Assignment 2, live run: the coordinator and four specialists on the real model.

    python run_live.py --dry-run --scenario ticket   # builds the wiring, no API call
    python run_live.py --scenario ticket             # four-concern ticket; one charge lookup fails
    python run_live.py --scenario ceiling            # $899 refund request; the hook must deny it

Needs ANTHROPIC_API_KEY in .env (or exported in the shell). Each live run costs API tokens.
Writes transcripts/live-<scenario>.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
from typing import Annotated

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from agent.model import MODEL_ID
from multi_agent.coordinator import load_prompt
from multi_agent.fixtures import TICKET
from multi_agent.hooks import build_hooks
from multi_agent.ledger import Ledger
from multi_agent.specs import SPECIALIST_SPECS
from multi_agent.tools import TOOL_FUNCS

ROOT = pathlib.Path(__file__).parent
MCP_SERVER = "support"
PREFIX = f"mcp__{MCP_SERVER}__"

SCENARIOS = {
    "ticket": TICKET,
    "ceiling": (
        "Hi, it's Bob Okafor (bob@example.com). The professional stand mixer from ORD-456 "
        "arrived with the housing crushed on one side. I want the full $899 back."
    ),
}

DESCRIPTIONS = {
    "lookup_order": "Fetch one order by ORDER ID. Returns customer_id, item, status, total and delivery age. Use it to get the customer_id.",
    "find_charges": "List the charges on one order. Needs order_id and customer_id (format CUST-001, never an email address).",
    "read_policy": "Return the returns policy: the window length and when it starts.",
    "process_refund": "Refund one delivered order. Use the order total from lookup_order when the order is one item. Refused refunds come back with the reason.",
    "escalate_ticket": "Write a handoff ticket for a human. Needs customer_id (format CUST-001), order, root cause and recommended action.",
}

SCHEMAS = {
    "lookup_order": {"order_id": Annotated[str, "Order ID, format ORD-123"]},
    "find_charges": {
        "order_id": Annotated[str, "Order ID, format ORD-123"],
        "customer_id": Annotated[str, "Customer ID, format CUST-001. Never an email address."],
    },
    "read_policy": {},
    "process_refund": {
        "order_id": Annotated[str, "Order ID, format ORD-123"],
        "customer_id": Annotated[str, "Customer ID, format CUST-001. Never an email address."],
        "amount_usd": Annotated[float, "Refund amount in US dollars, e.g. 149.99"],
        "reason": Annotated[str, "Short reason, e.g. 'cracked on arrival'"],
    },
    "escalate_ticket": {
        "customer_id": Annotated[str, "Customer ID, format CUST-001. Never an email address."],
        "order_id": Annotated[str, "Order ID, format ORD-123"],
        "amount_usd": Annotated[float, "Disputed amount in US dollars. Never a placeholder."],
        "root_cause": Annotated[str, "What happened and why this needs a person"],
        "recommended_action": Annotated[str, "What the human should do next"],
        "concern_id": Annotated[str, "Concern id from the ticket, e.g. C4"],
    },
}


def load_dotenv(path: pathlib.Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_server(ledger: Ledger):
    """The five tools as an in-process MCP server. The same tool code as the offline run."""
    def make_tool(name):
        @tool(name, DESCRIPTIONS[name], SCHEMAS[name])
        async def handler(args):
            result = TOOL_FUNCS[name](args, ledger)
            ledger.trace(f"   result: {'ok' if result['ok'] else result['errorCategory']} {json.dumps(result)}")
            return {"content": [{"type": "text", "text": json.dumps(result)}], "is_error": not result["ok"]}
        return handler

    return create_sdk_mcp_server(MCP_SERVER, "1.0.0", tools=[make_tool(n) for n in TOOL_FUNCS])


def trace_hook_factory(ledger: Ledger):
    """Records every tool call with the agent that made it, and denies MCP tool calls from the coordinator.

    agent_type is set only inside a subagent, so a call without it came from the coordinator.
    """
    async def trace_hook(input_data, tool_use_id, context):
        agent = input_data.get("agent_type")
        tool_name = input_data.get("tool_name", "?")
        ledger.trace(f"[{agent or 'coordinator'}] {tool_name}({json.dumps(input_data.get('tool_input', {}), default=str)})")
        if tool_name.startswith(PREFIX) and not agent:
            ledger.trace("   denied: the coordinator has no tools; it must delegate")
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "The coordinator has no tools. Delegate this to a specialist with the Agent tool.",
            }}
        return {}
    return trace_hook


def build_live_hooks(ledger: Ledger) -> dict:
    hooks = build_hooks(ledger, matcher=PREFIX + "process_refund")
    hooks["PreToolUse"].append(HookMatcher(matcher=None, hooks=[trace_hook_factory(ledger)]))
    return hooks


def live_agents() -> dict:
    """The four specialists. Tool names are the MCP names; each names the server by string (JSON-safe)."""
    return {
        name: AgentDefinition(
            description=spec.description,
            prompt=spec.prompt,
            tools=[PREFIX + t for t in spec.tools],
            mcpServers=[MCP_SERVER],
        )
        for name, spec in SPECIALIST_SPECS.items()
    }


def build_options(ledger: Ledger) -> ClaudeAgentOptions:
    server = make_server(ledger)
    return ClaudeAgentOptions(
        system_prompt=load_prompt("after"),
        agents=live_agents(),
        mcp_servers={MCP_SERVER: server},
        # Agent/Task launch subagents; the MCP tools are allowed, but the hook keeps them from the coordinator.
        allowed_tools=["Agent", "Task"] + [PREFIX + t for t in TOOL_FUNCS],
        # tools limits what the coordinator can use at all: only delegation, no messaging or built-ins.
        tools=["Agent", "Task"],
        hooks=build_live_hooks(ledger),
        max_turns=40,
        model=MODEL_ID,
    )


async def run(scenario: str, dry_run: bool) -> int:
    ledger = Ledger(charge_failures_left=2 if scenario == "ticket" else 0)
    options = build_options(ledger)
    if dry_run:
        print("Dry run: options built, no API call.")
        print("subagents:", sorted(options.agents))
        for name, agent in sorted(options.agents.items()):
            print(f"  {name}: tools={agent.tools}")
        print("coordinator MCP servers:", list(options.mcp_servers))
        import dataclasses, json as _json
        _json.dumps({k: dataclasses.asdict(v) for k, v in options.agents.items()})
        print("agent definitions serialize to JSON: ok")
        print("coordinator allowed tools:", options.allowed_tools)
        print("PreToolUse matchers:", [m.matcher for m in options.hooks["PreToolUse"]])
        return 0

    lines = [f"# Assignment 2 live transcript: {scenario}", f"Model: {MODEL_ID}", "",
             "Customer message:", f"> {SCENARIOS[scenario]}", ""]
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
              f"tickets: {[t['ticket_id'] for t in ledger.tickets] or 'none'}",
              "call trace (agent that made each call):", *ledger.trace_lines, "```"]
    if result is not None:
        lines += ["", "## Usage", "```text",
                  f"turns: {result.num_turns}  is_error: {result.is_error}  cost_usd: {result.total_cost_usd}",
                  f"usage: {json.dumps(result.usage, default=str)}", "```"]

    out = ROOT / "transcripts" / f"live-{scenario}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[{scenario}] refunds={len(ledger.refunds)} denials={len(ledger.denials)} "
          f"tickets={len(ledger.tickets)} cost_usd={getattr(result, 'total_cost_usd', None)}")
    print(f"wrote {out}")
    return 1 if result is None or result.is_error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="ticket")
    parser.add_argument("--dry-run", action="store_true", help="build the wiring without calling the API")
    args = parser.parse_args()
    load_dotenv()
    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error("ANTHROPIC_API_KEY is not set. Add it to .env or export it first.")
    return asyncio.run(run(args.scenario, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
