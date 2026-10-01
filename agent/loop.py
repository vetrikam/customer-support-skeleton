"""The agentic loop.

Control flow is driven entirely by `stop_reason`:

    tool_use        execute EVERY tool_use block, append ALL tool_results in one
                    user message, go round again
    end_turn        terminal: return the answer           (primary termination)
    stop_sequence   terminal, same as end_turn
    pause_turn      resumable: send the history back unchanged and continue
    refusal         terminal, but returned flagged as a refusal, not an answer
    max_tokens      raise LoopError: a truncated answer is not an answer
    anything else   raise LoopError: unknown is loud, never a silent break

MAX_TURNS is a labelled safety net. Reaching it raises; it never produces a reply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import enforcement
from .errors import TRANSIENT, VALIDATION, fail
from .handlers import HANDLERS
from .model import AgentResponse, Block
from .session import Session
from .tools import TOOLS

# SAFETY NET, NOT CONTROL FLOW. The loop ends because of stop_reason (end_turn /
# stop_sequence / refusal), never because of this number. A healthy run here takes
# 3-7 turns. If the cap is ever reached, something is wrong (a retry storm, a model
# looping on a blocked tool), so hitting it raises LoopError loudly instead of
# returning whatever text happens to be lying around as if it were an answer.
MAX_TURNS = 12

# stop_reasons that mean "the model has finished its reply".
TERMINAL_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})

SYSTEM_PROMPT = (
    "You are the customer support resolution agent for Ridgeline Home, a mid-size "
    "e-commerce retailer selling kitchen and homeware. You speak with customers directly.\n"
    "\n"
    "Work from the tools. Read each tool's description before choosing it, and read the "
    "structured result you get back: `errorCategory` and `isRetryable` tell you what to do "
    "next. When a customer raises several issues in one message, deal with all of them and "
    "answer once, at the end, in a single reply.\n"
    "\n"
    "Be warm, brief and concrete. Give real numbers, order IDs, refund IDs and ticket "
    "numbers rather than vague reassurance. Never promise an outcome you have not already "
    "obtained from a tool.\n"
    "\n"
    "# DO NOT ADD THE IDENTITY RULE OR THE $500 CEILING TO THIS PROMPT.\n"
    "# Both are graded on being enforced in code. See agent/enforcement.py."
)


class LoopError(RuntimeError):
    """The loop could not produce a trustworthy answer. Never swallowed silently."""

    def __init__(self, message: str, *, turns: int = 0, stop_reason: str | None = None):
        super().__init__(message)
        self.turns = turns
        self.stop_reason = stop_reason


@dataclass
class Result:
    final_text: str
    turns: int
    session: Session
    # Why the loop ended: "end_turn", "stop_sequence" or "refusal". Callers that
    # need to treat a refusal differently (route to a human, alert) can read this.
    stop_reason: str = "end_turn"


def execute_tool(block: Block, session: Session, recorder=None) -> dict:
    """Run one tool_use block through the enforcement hook and its handler."""
    blocked = enforcement.intercept(block.name, block.input, session)

    if blocked is not None:
        # The handler is never called. This is the whole point of the hook.
        payload = blocked
    else:
        handler = HANDLERS.get(block.name)
        if handler is None:
            payload = fail(VALIDATION, f"There is no tool named '{block.name}'.")
        else:
            try:
                payload = handler(session, **block.input)
            except TypeError as exc:
                payload = fail(
                    VALIDATION,
                    f"Wrong arguments for {block.name}: {exc}. Check the tool's input schema.",
                )
            except Exception:
                # A tool must never raise into the loop.
                payload = fail(
                    TRANSIENT,
                    f"{block.name} failed unexpectedly. Retry once; if it fails again, "
                    "escalate to a human.",
                )

    session.log(
        event="tool_call",
        tool=block.name,
        input=block.input,
        ok=payload["ok"],
        category=payload.get("errorCategory"),
    )
    if recorder:
        recorder.tool_result(block, payload, blocked=blocked is not None)

    return {
        "type": "tool_result",
        "tool_use_id": block.id,  # must match the tool_use block it answers
        "content": json.dumps(payload),
        "is_error": not payload["ok"],
    }


def run_agent(
    *,
    user_message: str,
    model,
    session: Session,
    system: str = SYSTEM_PROMPT,
    tools: list[dict] | None = None,
    recorder=None,
) -> Result:
    tools = TOOLS if tools is None else tools
    messages: list[dict] = [{"role": "user", "content": user_message}]
    if recorder:
        recorder.user(user_message)

    turn = 0
    while turn < MAX_TURNS:
        turn += 1

        response: AgentResponse = model.create(system=system, tools=tools, messages=messages)
        if recorder:
            recorder.assistant(response, turn)

        stop = response.stop_reason

        # max_tokens: the reply was cut off mid-thought. A truncated answer is not
        # an answer, and if it was cut inside a tool_use block the call is
        # incomplete. Fail loudly rather than send half a sentence to a customer.
        if stop == "max_tokens":
            raise LoopError(
                f"Response truncated at max_tokens on turn {turn}; refusing to return a "
                "partial answer.",
                turns=turn,
                stop_reason=stop,
            )

        # Every other recognised stop_reason keeps the assistant turn in history.
        messages.append({"role": "assistant", "content": response.history_content})

        # end_turn / stop_sequence: the model has finished. This is the PRIMARY
        # termination path.
        if stop in TERMINAL_STOP_REASONS:
            return Result(
                final_text=response.text, turns=turn, session=session, stop_reason=stop
            )

        # refusal: the model declined. Surface it as a refusal (stop_reason on the
        # Result, an audit entry), do not dress it up as a normal resolution.
        if stop == "refusal":
            session.log(event="refusal", turn=turn)
            return Result(
                final_text=response.text
                or "I'm not able to help with this request here. Please contact our "
                "support team directly.",
                turns=turn,
                session=session,
                stop_reason=stop,
            )

        # pause_turn: a server-side tool paused a long turn. It is resumable:
        # send the conversation back as-is (assistant content already appended,
        # no new user message) and let the model carry on.
        if stop == "pause_turn":
            if recorder:
                recorder.note(f"_Turn {turn} paused by the server; resuming._")
            continue

        if stop == "tool_use":
            # Execute EVERY tool_use block, and return ALL results in ONE user
            # message. Splitting them across messages teaches the model to stop
            # batching calls, which multi-concern handling depends on.
            results = [execute_tool(block, session, recorder) for block in response.tool_uses]
            if not results:
                raise LoopError(
                    f"stop_reason was tool_use on turn {turn} but no tool_use block was "
                    "present.",
                    turns=turn,
                    stop_reason=stop,
                )
            messages.append({"role": "user", "content": results})
            if recorder:
                recorder.tool_results_appended(len(results))
            continue

        # Anything unrecognised is loud, never a silent fall-through.
        raise LoopError(
            f"Unrecognised stop_reason {stop!r} on turn {turn}.", turns=turn, stop_reason=stop
        )

    # The only way to reach this line is the safety net catching a runaway loop.
    # A safety net that catches something is an incident, not an answer.
    session.log(event="safety_net", turns=turn)
    raise LoopError(
        f"Safety net: no terminal stop_reason after {MAX_TURNS} turns. The run was "
        "stopped rather than returning a partial answer.",
        turns=turn,
        stop_reason="max_turns",
    )
