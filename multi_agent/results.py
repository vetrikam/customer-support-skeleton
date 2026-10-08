"""Output contract for Assignment 2.

Every specialist returns Findings (claim, evidence, source, all tied to a concern id)
or a Failure. Nothing comes back as free prose, so attribution can be checked after
the coordinator summarises.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    concern_id: str
    claim: str
    evidence: str
    source: str

    def __post_init__(self) -> None:
        for name in ("concern_id", "claim", "evidence", "source"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"finding is missing '{name}': {self}")


@dataclass
class Failure:
    concern_id: str
    agent: str
    errorCategory: str
    isRetryable: bool
    message: str
    attempted: list = field(default_factory=list)
    partial: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)


@dataclass
class Outcome:
    concern_id: str
    agent: str
    findings: list = field(default_factory=list)
    failure: Failure | None = None
    status: str = "resolved"  # resolved | escalated | unresolved
    prompt: str = ""          # exactly what the subagent was given
