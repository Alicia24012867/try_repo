"""Champion lineage path helpers for accumulated Flow Agent evolution."""

from __future__ import annotations

from pathlib import Path

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_ABC_BIN,
    FLOWTUNE_SOURCE_ROOT,
)
from scripts.agents.self_evolved_abc.flow.paths import repo_path


SOURCE_ROOT_ASSIGNMENT_KEYS = ("base_source_root", "champion_source_root")
BASELINE_BINARY_ASSIGNMENT_KEYS = ("baseline_abc_bin", "champion_abc_bin")


def resolve_assignment_path(
    context: CycleContext,
    *,
    explicit: Path | None = None,
    assignment_keys: tuple[str, ...] = (),
    default: Path | None = None,
) -> Path:
    """Resolve an explicit, assignment-provided, or default repo path."""

    if explicit is not None:
        return repo_path(context, explicit)
    for key in assignment_keys:
        value = str(context.assignment.get(key, "")).strip()
        if value:
            return repo_path(context, Path(value))
    if default is None:
        raise ValueError("no assignment path or default path was provided")
    return repo_path(context, default)


def resolve_baseline_abc_bin(
    context: CycleContext,
    *,
    explicit: Path | None = None,
) -> Path:
    """Return the baseline binary for comparison, preferring the champion."""

    return resolve_assignment_path(
        context,
        explicit=explicit,
        assignment_keys=BASELINE_BINARY_ASSIGNMENT_KEYS,
        default=DEFAULT_ABC_BIN,
    )


def resolve_base_source_root(context: CycleContext) -> Path:
    """Return the source tree used to seed the next candidate workspace."""

    return resolve_assignment_path(
        context,
        assignment_keys=SOURCE_ROOT_ASSIGNMENT_KEYS,
        default=FLOWTUNE_SOURCE_ROOT,
    )


def existing_base_source_root(context: CycleContext) -> Path | None:
    """Return assignment-provided source root only when it exists locally."""

    for key in SOURCE_ROOT_ASSIGNMENT_KEYS:
        value = str(context.assignment.get(key, "")).strip()
        if not value:
            continue
        resolved = repo_path(context, Path(value))
        if resolved.is_dir():
            return resolved
    return None


def source_context_path(context: CycleContext, repo_relative: Path) -> Path:
    """Map a repo source path into the current champion source tree when present."""

    base_source = existing_base_source_root(context)
    if base_source is None:
        return repo_path(context, repo_relative)
    try:
        suffix = repo_relative.relative_to(FLOWTUNE_SOURCE_ROOT)
    except ValueError:
        return repo_path(context, repo_relative)
    return base_source / suffix
