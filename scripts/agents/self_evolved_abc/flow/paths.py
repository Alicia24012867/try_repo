"""Canonical paths for the Flow Agent paper-reproduction workflow."""

from __future__ import annotations

from pathlib import Path

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    IMPL_BASELINE_LABEL,
    IMPL_CANDIDATE_LABEL,
    IMPL_COMPARISON_LABEL,
)


def cycle_dir(context: CycleContext) -> Path:
    return context.repo_root / "experiments" / context.cycle_id


def agents_dir(context: CycleContext) -> Path:
    return cycle_dir(context) / "agents"


def logs_dir(context: CycleContext) -> Path:
    return cycle_dir(context) / "logs"


def outputs_dir(context: CycleContext) -> Path:
    return cycle_dir(context) / "outputs"


def results_dir(context: CycleContext) -> Path:
    return cycle_dir(context) / "results"


def impl_compare_root(context: CycleContext) -> Path:
    return cycle_dir(context) / "impl_compare"


def impl_baseline_dir(context: CycleContext) -> Path:
    return impl_compare_root(context) / IMPL_BASELINE_LABEL


def impl_candidate_dir(context: CycleContext) -> Path:
    return impl_compare_root(context) / IMPL_CANDIDATE_LABEL


def impl_comparison_dir(context: CycleContext) -> Path:
    return impl_compare_root(context) / IMPL_COMPARISON_LABEL


def candidate_workspace_root(context: CycleContext) -> Path:
    return impl_candidate_dir(context) / "workspace"


def repo_path(context: CycleContext, path: Path) -> Path:
    if path.is_absolute():
        resolved = path.resolve()
        try:
            resolved.relative_to(context.repo_root)
        except ValueError as exc:
            raise ValueError(f"path escapes repository: {path}") from exc
        return resolved
    return context.resolve_repo_path(str(path))


def repo_relative_path(context: CycleContext, path: Path) -> Path:
    return repo_path(context, path).relative_to(context.repo_root)


def repo_relative_existing_path(context: CycleContext, path: Path) -> Path:
    resolved = repo_path(context, path)
    if not resolved.exists():
        raise FileNotFoundError(f"expected path does not exist: {path}")
    return resolved.relative_to(context.repo_root)


def ensure_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
