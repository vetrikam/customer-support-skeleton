"""Dispatches one tool call with the same two checks the SDK applies:
the agent's tools list, then the PreToolUse hooks. Offline stand-in for the SDK loop."""

from __future__ import annotations

from dataclasses import dataclass

from agent.errors import BUSINESS, PERMISSION, VALIDATION, fail

from .ledger import Ledger
from .tools import TOOL_FUNCS


@dataclass
class Worker:
    name: str
    allowed: list | None  # AgentDefinition.tools; None means the field was omitted
    ledger: Ledger
    hooks: dict

    async def call(self, tool: str, args: dict) -> dict:
        return await call_tool(self.name, self.allowed, tool, args, self.ledger, self.hooks)


async def call_tool(agent_name, allowed, tool, args, ledger, hooks) -> dict:
    if tool not in TOOL_FUNCS:
        result = fail(VALIDATION, f"Unknown tool '{tool}'.")
    elif allowed is not None and tool not in allowed:
        result = fail(PERMISSION, f"'{tool}' is not in {agent_name}'s tool list, so it cannot be used here.")
    else:
        result = None
        for matcher in hooks.get("PreToolUse", []):
            if matcher.matcher not in (None, tool):
                continue
            for hook in matcher.hooks:
                out = await hook({"tool_name": tool, "tool_input": args, "agent_type": agent_name}, None, None)
                spec = out.get("hookSpecificOutput", {})
                if spec.get("permissionDecision") == "deny":
                    result = fail(BUSINESS, spec["permissionDecisionReason"], denied_by="hook:refund_ceiling")
                    break
            if result is not None:
                break
        if result is None:
            result = TOOL_FUNCS[tool](args, ledger)
    ledger.trace(f"[{agent_name}] {tool}({args}) -> {'ok' if result['ok'] else result['errorCategory']}")
    return result
