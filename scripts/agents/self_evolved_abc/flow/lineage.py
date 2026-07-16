"""Champion lineage path helpers for accumulated Flow Agent evolution."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

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
    """Return the frozen source tree used by both prompting and patching.

    New paired assignments bind their evaluation baseline in ``baseline_ref``.
    The legacy top-level fields remain useful to the runtime, but they must
    resolve to that same snapshot instead of silently selecting another tree.
    """

    if "baseline_ref" in context.assignment:
        baseline_ref = context.assignment.get("baseline_ref")
        if not isinstance(baseline_ref, Mapping):
            raise ValueError("assignment baseline_ref is not an object")
        source_value = str(baseline_ref.get("source_root", "")).strip()
        if not source_value:
            raise ValueError("assignment baseline_ref is missing source_root")
        source_root = repo_path(context, Path(source_value))
        for key in SOURCE_ROOT_ASSIGNMENT_KEYS:
            alias = str(context.assignment.get(key, "")).strip()
            if alias and repo_path(context, Path(alias)) != source_root:
                raise ValueError(
                    "assignment source root diverges from frozen baseline_ref "
                    f"at {key}"
                )
        return source_root

    return resolve_assignment_path(
        context,
        assignment_keys=SOURCE_ROOT_ASSIGNMENT_KEYS,
        default=FLOWTUNE_SOURCE_ROOT,
    )


def existing_base_source_root(context: CycleContext) -> Path | None:
    """Return the resolved frozen baseline root only when it exists locally."""

    resolved = resolve_base_source_root(context)
    return resolved if resolved.is_dir() else None


def source_context_path(context: CycleContext, repo_relative: Path) -> Path:
    """Map a logical patch path into the exact frozen baseline source tree."""

    logical_root = repo_path(context, FLOWTUNE_SOURCE_ROOT)
    logical_path = repo_path(context, repo_relative)
    claims_flowtune_scope = (
        repo_relative == FLOWTUNE_SOURCE_ROOT
        or FLOWTUNE_SOURCE_ROOT in repo_relative.parents
    )
    try:
        suffix = logical_path.relative_to(logical_root)
    except ValueError:
        if claims_flowtune_scope:
            raise ValueError(
                "source context path escapes the FlowTune source root: "
                f"{repo_relative}"
            )
        return logical_path

    base_source = resolve_base_source_root(context)
    if not base_source.is_dir():
        relative = base_source.relative_to(context.repo_root)
        raise FileNotFoundError(
            f"frozen baseline source root is missing: {relative}"
        )
    candidate = (base_source / suffix).resolve()
    try:
        candidate.relative_to(base_source)
    except ValueError as exc:
        raise ValueError(
            "source context path escapes the frozen baseline source root: "
            f"{repo_relative}"
        ) from exc
    return candidate
