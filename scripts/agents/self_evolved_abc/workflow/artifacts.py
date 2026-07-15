"""Candidate-safe artifact layout shared by Flow and Logic workflows."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.agents.self_evolved_abc.cycle_context import CycleContext


LEGACY_CYCLE_LAYOUT = "legacy_cycle_v1"
CANDIDATE_SCOPED_LAYOUT = "candidate_scoped_v2"
SUPPORTED_LAYOUTS = frozenset((LEGACY_CYCLE_LAYOUT, CANDIDATE_SCOPED_LAYOUT))
PORTFOLIO_CYCLE_RE = re.compile(r"^cycle_[0-9]{3,}$")


def validate_candidate_id(value: object) -> str:
    """Return a filesystem-safe candidate id or raise ``ValueError``."""

    candidate_id = str(value or "").strip()
    if (
        not candidate_id
        or not candidate_id[0].isalnum()
        or any(
            not (character.isalnum() or character in ("_", "-"))
            for character in candidate_id
        )
    ):
        raise ValueError(f"invalid candidate_id: {value!r}")
    return candidate_id


def validate_portfolio_cycle_id(value: object) -> str:
    """Require the canonical cycle_NNN namespace used by paired campaigns."""

    cycle_id = str(value or "").strip()
    if not PORTFOLIO_CYCLE_RE.fullmatch(cycle_id):
        raise ValueError(f"invalid portfolio cycle_id: {value!r}")
    return cycle_id


def safe_repo_path(repo_root: Path, path: Path) -> Path:
    """Resolve existing symlink ancestors and reject repository escapes."""

    root = repo_root.resolve()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact path escapes repository: {path}") from exc
    return resolved


def artifact_layout(context: CycleContext) -> str:
    """Resolve layout, preserving legacy paths for old assignments."""

    value = str(
        context.assignment.get("artifact_layout", LEGACY_CYCLE_LAYOUT)
    ).strip()
    if value not in SUPPORTED_LAYOUTS:
        raise ValueError(f"unsupported artifact_layout: {value!r}")
    return value


def implementation_root(context: CycleContext) -> Path:
    """Return the isolated implementation-comparison root for a candidate."""

    cycle_root = context.repo_root / "experiments" / context.cycle_id
    if artifact_layout(context) == LEGACY_CYCLE_LAYOUT:
        return cycle_root / "impl_compare"
    candidate_id = validate_candidate_id(context.candidate_id)
    return cycle_root / "candidates" / candidate_id / "impl_compare"


def review_decision_path(context: CycleContext) -> Path:
    return implementation_root(context) / "comparison" / "review_decision.json"


def agent_attempt_path(
    context: CycleContext,
    attempt: int,
    artifact: str,
) -> Path:
    """Return a safe generated path for one Coding Agent attempt."""

    if attempt < 1:
        raise ValueError("agent attempt must be >= 1")
    if artifact not in ("assignment", "status"):
        raise ValueError(f"unsupported agent attempt artifact: {artifact!r}")
    cycle_id = validate_portfolio_cycle_id(context.cycle_id)
    candidate_id = validate_candidate_id(context.candidate_id)
    return safe_repo_path(
        context.repo_root,
        context.repo_root
        / "experiments"
        / cycle_id
        / "agents"
        / "attempts"
        / candidate_id
        / f"attempt_{attempt:02d}.{artifact}.json",
    )


def implementation_root_for(
    *,
    repo_root: Path,
    cycle_id: str,
    candidate_id: str,
    layout: str,
) -> Path:
    """Resolve another cycle using the same deterministic layout contract."""

    if layout not in SUPPORTED_LAYOUTS:
        raise ValueError(f"unsupported artifact_layout: {layout!r}")
    cycle_root = repo_root.resolve() / "experiments" / str(cycle_id)
    if layout == LEGACY_CYCLE_LAYOUT:
        return cycle_root / "impl_compare"
    return cycle_root / "candidates" / validate_candidate_id(candidate_id) / "impl_compare"
