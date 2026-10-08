"""Session state the model cannot edit. Refunds are recorded only on success."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ledger:
    charge_failures_left: int = 0  # simulate a payments outage for this many calls
    refunds: list = field(default_factory=list)  # successful refunds only
    refunded_by_order: dict = field(default_factory=dict)
    denials: list = field(default_factory=list)  # hook denials, recorded as data
    tickets: list = field(default_factory=list)
    trace_lines: list = field(default_factory=list)

    def trace(self, line: str) -> None:
        self.trace_lines.append(line)
