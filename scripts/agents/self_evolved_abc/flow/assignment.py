"""Flow Agent assignment construction and scope normalization helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_CANDIDATE_ABC_FLOW,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_CANDIDATE_SOURCE_PATCH_TODO,
    FLOW_INFRA_ALLOWED_ROOTS,
    FLOW_SOURCE_TOUCHPOINTS,
    FLOWTUNE_ABCI_SCOPE,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
)
from scripts.agents.self_evolved_abc.flow.promotion import (
    DEFAULT_PROMOTION_THRESHOLDS,
)
from scripts.agents.self_evolved_abc.flow.multi_flow import (
    default_evaluation_flows,
    default_flow_aggregation,
    normalized_evaluation_flows,
    normalize_flow_aggregation,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    CANDIDATE_SCOPED_LAYOUT,
    LEGACY_CYCLE_LAYOUT,
    SUPPORTED_LAYOUTS,
    validate_candidate_id,
)


FLOW_CYCLE_DIRS = (
    "agents/assignments",
    "agents/plans",
    "agents/candidate_changes",
    "agents/source_patch_todos",
    "agents/source_patches",
    "agents/feedback",
    "agents/rule_updates",
    "logs",
    "outputs",
    "results",
    "impl_compare",
)

FLOW_SOURCE_PATCH_MODES = (
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_CANDIDATE_ABC_FLOW,
    FLOW_CANDIDATE_SOURCE_PATCH_TODO,
)


def flow_cycle_write_roots(
    cycle_id: str,
    *,
    candidate_id: str = "",
    artifact_layout: str = LEGACY_CYCLE_LAYOUT,
) -> tuple[str, ...]:
    """Return roots the runner may populate for one active cycle."""

    if artifact_layout not in SUPPORTED_LAYOUTS:
        raise ValueError(f"unsupported artifact_layout: {artifact_layout!r}")
    implementation_root = f"experiments/{cycle_id}/impl_compare"
    if artifact_layout == CANDIDATE_SCOPED_LAYOUT:
        implementation_root = (
            f"experiments/{cycle_id}/candidates/"
            f"{validate_candidate_id(candidate_id)}/impl_compare"
        )
    return (
        f"experiments/{cycle_id}/agents",
        f"experiments/{cycle_id}/logs",
        f"experiments/{cycle_id}/outputs",
        f"experiments/{cycle_id}/results",
        implementation_root,
    )


def default_source_patch_allowed_roots(
    mode: str,
    requested_roots: Iterable[object] = (),
) -> tuple[str, ...]:
    """Return source roots for a Flow Agent source-patch assignment."""

    roots = _clean_paths(requested_roots)
    if roots:
        return roots
    if mode == FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
        return (FLOWTUNE_SOURCE_SCOPE_PRIMARY, FLOWTUNE_ABCI_SCOPE)
    return ()


def canonical_flow_subsystem(mode: str, subsystem: object | None) -> str:
    """Keep source-patch assignments pointed at real FlowTune source."""

    text = str(subsystem or "").strip()
    if mode == FLOW_CANDIDATE_SOURCE_PATCH_DIFF and text in ("", "configs/flows"):
        return FLOWTUNE_SOURCE_SCOPE_PRIMARY
    return text or "configs/flows"


def build_flow_allowed_to_edit(
    *,
    cycle_id: str,
    mode: str,
    subsystem: object | None,
    source_patch_allowed_roots: Iterable[object] = (),
    existing: Iterable[object] = (),
    candidate_id: str = "",
    artifact_layout: str = LEGACY_CYCLE_LAYOUT,
) -> tuple[str, ...]:
    """Build an ordered, de-duplicated edit scope for Flow Agent work."""

    legacy_impl_root = f"experiments/{cycle_id}/impl_compare"
    candidate_impl_prefix = f"experiments/{cycle_id}/candidates/"
    existing_paths = tuple(
        path
        for path in existing
        if str(path).rstrip("/") != legacy_impl_root
        and not (
            str(path).startswith(candidate_impl_prefix)
            and str(path).rstrip("/").endswith("/impl_compare")
        )
    )
    roots: list[object] = [
        *flow_cycle_write_roots(
            cycle_id,
            candidate_id=candidate_id,
            artifact_layout=artifact_layout,
        ),
        "configs/flows",
        canonical_flow_subsystem(mode, subsystem),
    ]
    if mode == FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
        roots.extend(
            default_source_patch_allowed_roots(mode, source_patch_allowed_roots)
        )
        existing_paths = tuple(
            path
            for path in existing_paths
            if str(path).strip() not in FLOW_INFRA_ALLOWED_ROOTS
        )
    else:
        roots.extend(FLOW_INFRA_ALLOWED_ROOTS)
    roots.extend(existing_paths)
    return _clean_paths(roots)


def normalize_flow_assignment_scope(
    assignment: Mapping[str, Any],
) -> dict[str, object]:
    """Return an assignment with self-consistent Flow Agent scope fields."""

    payload: dict[str, object] = dict(assignment)
    cycle_id = str(payload.get("cycle_id", "")).strip()
    mode = str(payload.get("source_patch_mode", FLOW_CANDIDATE_ABC_FLOW)).strip()
    if mode not in FLOW_SOURCE_PATCH_MODES:
        mode = FLOW_CANDIDATE_ABC_FLOW

    source_roots = default_source_patch_allowed_roots(
        mode,
        payload.get("source_patch_allowed_roots", ()),
    )
    subsystem = canonical_flow_subsystem(mode, payload.get("subsystem"))
    candidate_id = str(payload.get("candidate_id", "")).strip()
    artifact_layout = str(
        payload.get("artifact_layout", LEGACY_CYCLE_LAYOUT)
    ).strip()

    payload["source_patch_mode"] = mode
    payload["subsystem"] = subsystem
    if source_roots:
        payload["source_patch_allowed_roots"] = list(source_roots)
    payload.setdefault("evaluation_flow_commands", list(DEFAULT_EVAL_FLOW_COMMANDS))
    payload["evaluation_flows"] = normalized_evaluation_flows(
        payload.get("evaluation_flows", default_evaluation_flows())
    )
    payload["flow_aggregation"] = normalize_flow_aggregation(
        payload.get("flow_aggregation", default_flow_aggregation())
    )
    payload.setdefault("flow_source_touchpoints", dict(FLOW_SOURCE_TOUCHPOINTS))
    payload.setdefault("promotion_thresholds", DEFAULT_PROMOTION_THRESHOLDS.as_dict())
    payload.setdefault(
        "repository_context_manifest",
        "configs/agents/context/repositories.json",
    )
    payload.setdefault("repository_context_max_repositories", 6)
    payload.setdefault("repository_context_files_per_repository", 3)
    payload.setdefault("repository_context_max_chars", 60_000)
    payload.setdefault("repository_context_min_available", 6)
    payload.setdefault("repository_context_enforce_minimum", True)

    if cycle_id:
        payload["allowed_to_edit"] = list(
            build_flow_allowed_to_edit(
                cycle_id=cycle_id,
                mode=mode,
                subsystem=subsystem,
                source_patch_allowed_roots=source_roots,
                existing=payload.get("allowed_to_edit", ()),
                candidate_id=candidate_id,
                artifact_layout=artifact_layout,
            )
        )
    return payload


def _clean_paths(values: Iterable[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    return tuple(cleaned)
