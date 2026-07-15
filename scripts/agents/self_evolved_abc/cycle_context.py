"""Cycle context and repository path helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.schemas import AgentArtifactPaths


@dataclass(frozen=True)
class CycleContext:
    """Repository-local context for one agent candidate."""

    repo_root: Path
    assignment: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", self.repo_root.resolve())

    @classmethod
    def from_assignment_file(
        cls, repo_root: Path, assignment_path: Path
    ) -> "CycleContext":
        payload = json.loads(assignment_path.read_text(encoding="utf-8"))
        return cls(repo_root=repo_root, assignment=payload)

    @property
    def cycle_id(self) -> str:
        return str(self.assignment["cycle_id"])

    @property
    def candidate_id(self) -> str:
        return str(self.assignment["candidate_id"])

    @property
    def agent_name(self) -> str:
        return str(self.assignment["agent_name"])

    @property
    def paper_role(self) -> str:
        return str(self.assignment["paper_role"])

    @property
    def benchmark_scope(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.assignment.get("benchmark_scope", ()))

    @property
    def evaluation_benchmark_scope(self) -> tuple[str, ...]:
        scope = self.assignment.get("evaluation_benchmark_scope", ())
        if scope:
            return tuple(str(item) for item in scope)
        return self.benchmark_scope

    @property
    def recent_evidence(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.assignment.get("recent_evidence", ()))

    def artifact_paths(self) -> AgentArtifactPaths:
        # Imported lazily because workflow.artifacts uses CycleContext for its
        # type-level path helpers.  Candidate ids become filenames here, so
        # validate them before any artifact path is constructed.
        from scripts.agents.self_evolved_abc.workflow.artifacts import (
            validate_candidate_id,
        )

        base = self.repo_root / "experiments" / self.cycle_id / "agents"
        name = f"{validate_candidate_id(self.candidate_id)}.md"
        return AgentArtifactPaths(
            plan=base / "plans" / name,
            candidate_change=base / "candidate_changes" / name,
            feedback=base / "feedback" / name,
            rule_update=base / "rule_updates" / name,
        )

    def resolve_repo_path(self, relative: str) -> Path:
        path = (self.repo_root / relative).resolve()
        try:
            path.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError(f"path escapes repository: {relative}") from exc
        return path

    def read_evidence_text(self) -> dict[str, str]:
        """Read assignment evidence files into a prompt-ready dictionary."""

        evidence: dict[str, str] = {}
        for relative in self.recent_evidence:
            path = self.resolve_repo_path(relative)
            if path.is_file():
                evidence[relative] = path.read_text(encoding="utf-8", errors="replace")
            elif path.is_dir():
                evidence[relative] = "TODO_DIRECTORY_EVIDENCE_SUMMARY"
            else:
                evidence[relative] = "TODO_MISSING_EVIDENCE"
        return evidence
