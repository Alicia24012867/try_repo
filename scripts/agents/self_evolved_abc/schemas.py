"""Shared data shapes for the paper-style agent scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_AGENT_DECISION = "NEEDS_HUMAN_REVIEW"


@dataclass(frozen=True)
class AgentArtifactPaths:
    """Canonical artifact paths for one agent candidate."""

    plan: Path
    candidate_change: Path
    feedback: Path
    rule_update: Path

    def ensure_parent_dirs(self) -> None:
        for path in (self.plan, self.candidate_change, self.feedback, self.rule_update):
            path.parent.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class AgentArtifacts:
    """Markdown artifacts produced by one agent run."""

    plan_markdown: str
    candidate_markdown: str
    feedback_markdown: str
    rule_update_markdown: str
    decision: str = DEFAULT_AGENT_DECISION


@dataclass(frozen=True)
class ValidationIssue:
    """One model-response validation issue."""

    field: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class FlowAgentResponse:
    """Validated Flow Agent model output."""

    rationale: str
    candidate_kind: str
    candidate_steps: tuple[str, ...]
    source_design: str
    expected_effect: str
    entry_points: tuple[str, ...]
    invariants: tuple[str, ...]
    risk_hotspots: tuple[str, ...]
    files_to_write: tuple[str, ...]
    compatibility_notes: Mapping[str, Any]
    validation_plan: tuple[str, ...]
    risks: tuple[str, ...]
    rollback_plan: str
    rule_updates: tuple[str, ...]
    decision: str


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating one agent model response."""

    ok: bool
    response: FlowAgentResponse | None
    issues: tuple[ValidationIssue, ...]
    decision: str


def markdown_bullets(items: Iterable[object]) -> str:
    values = [str(item) for item in items if str(item)]
    if not values:
        return "- None.\n"
    return "\n".join(f"- {item}" for item in values) + "\n"
