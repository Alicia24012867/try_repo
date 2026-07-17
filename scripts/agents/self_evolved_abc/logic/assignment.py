"""Scope normalization for paper-role Logic Minimization assignments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from scripts.agents.self_evolved_abc.flow.assignment import flow_cycle_write_roots
from scripts.agents.self_evolved_abc.flow.promotion import (
    DEFAULT_PROMOTION_THRESHOLDS,
)
from scripts.agents.self_evolved_abc.flow.multi_flow import (
    default_evaluation_flows,
    default_flow_aggregation,
    normalized_evaluation_flows,
    normalize_flow_aggregation,
)
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_ABCI_ROOT,
    LOGIC_AGENT_NAME,
    LOGIC_EVALUATION_FLOW_COMMANDS,
    LOGIC_PAPER_ROLE,
    LOGIC_SOURCE_PATCH_MODE,
    LOGIC_SOURCE_TOUCHPOINTS,
    normalize_logic_target_command,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import LEGACY_CYCLE_LAYOUT


def normalize_logic_assignment_scope(
    assignment: Mapping[str, Any],
) -> dict[str, object]:
    """Return a deterministic, non-overlapping Logic assignment.

    Planner text may select the hypothesis and one of the reachable pass
    families.  It cannot change role identity, candidate representation, or
    the paper-owned ABCI source root.  This fail-closed normalization is also
    what keeps concurrently dispatched Flow and Logic branches disjoint.
    """

    payload: dict[str, object] = dict(assignment)
    cycle_id = str(payload.get("cycle_id", "")).strip()
    candidate_id = str(payload.get("candidate_id", "")).strip()
    artifact_layout = str(
        payload.get("artifact_layout", LEGACY_CYCLE_LAYOUT)
    ).strip()
    target_command = normalize_logic_target_command(
        _planning_target(payload) or payload.get("target_command")
    )

    payload.update(
        {
            "agent_name": LOGIC_AGENT_NAME,
            "paper_role": LOGIC_PAPER_ROLE,
            "subsystem": LOGIC_ABCI_ROOT,
            "source_patch_mode": LOGIC_SOURCE_PATCH_MODE,
            "source_patch_allowed_roots": [LOGIC_ABCI_ROOT],
            "planner_approved_source_roots": [],
            "planner_approved_new_source_files": False,
            "planner_approved_build_metadata": False,
            "target_command": target_command,
            "target_source_dir": LOGIC_ABCI_ROOT,
            "logic_source_touchpoints": _json_touchpoints(),
            "diagnostic_flow_commands": list(LOGIC_EVALUATION_FLOW_COMMANDS),
        }
    )

    frozen_commands = _frozen_evaluation_commands(payload)
    payload["evaluation_flow_commands"] = list(
        frozen_commands or LOGIC_EVALUATION_FLOW_COMMANDS
    )
    payload["evaluation_flows"] = normalized_evaluation_flows(
        payload.get("evaluation_flows", default_evaluation_flows())
    )
    payload["flow_aggregation"] = normalize_flow_aggregation(
        payload.get("flow_aggregation", default_flow_aggregation())
    )
    payload.setdefault(
        "promotion_thresholds", DEFAULT_PROMOTION_THRESHOLDS.as_dict()
    )

    # The prompt's repository-knowledge layer is deliberately bounded but
    # large enough to include several independent, pinned implementation
    # precedents in addition to the local FlowTune source of truth.
    payload.setdefault(
        "repository_context_manifest",
        "configs/agents/context/repositories.json",
    )
    payload.setdefault("repository_context_max_repositories", 9)
    payload.setdefault("repository_context_files_per_repository", 3)
    payload.setdefault("repository_context_max_chars", 72_000)
    payload.setdefault("repository_context_min_available", 9)
    payload.setdefault("repository_context_enforce_minimum", True)
    # Query terms are target-derived coordinator state, not sticky user/model
    # input.  Recompute them whenever Planning rotates the Logic family.
    payload["repository_context_query_terms"] = _target_query_terms(
        target_command
    )

    planning_meta = payload.get("_planning_meta")
    normalized_meta = (
        dict(planning_meta) if isinstance(planning_meta, Mapping) else {}
    )
    normalized_meta.update(
        {
            "target_command": target_command,
            "target_source_dir": LOGIC_ABCI_ROOT,
        }
    )
    payload["_planning_meta"] = normalized_meta

    allowed: list[str] = []
    if cycle_id:
        allowed.extend(
            flow_cycle_write_roots(
                cycle_id,
                candidate_id=candidate_id,
                artifact_layout=artifact_layout,
            )
        )
    allowed.append(LOGIC_ABCI_ROOT)
    payload["allowed_to_edit"] = _deduplicate(allowed)
    return payload


def build_logic_allowed_to_edit(
    *,
    cycle_id: str,
    candidate_id: str = "",
    artifact_layout: str = LEGACY_CYCLE_LAYOUT,
) -> tuple[str, ...]:
    """Return runner artifact roots plus the one Logic-owned source root."""

    roots = flow_cycle_write_roots(
        cycle_id,
        candidate_id=candidate_id,
        artifact_layout=artifact_layout,
    )
    return tuple((*roots, LOGIC_ABCI_ROOT))


def _planning_target(payload: Mapping[str, object]) -> object:
    meta = payload.get("_planning_meta")
    if isinstance(meta, Mapping):
        return meta.get("target_command")
    return None


def _frozen_evaluation_commands(
    payload: Mapping[str, object],
) -> tuple[str, ...]:
    contract = payload.get("evaluation_contract")
    if not isinstance(contract, Mapping):
        return ()
    commands = contract.get("flow_commands")
    if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)):
        return ()
    cleaned = tuple(str(item).strip() for item in commands if str(item).strip())
    return cleaned


def _json_touchpoints() -> dict[str, list[str]]:
    return {
        command: list(paths)
        for command, paths in LOGIC_SOURCE_TOUCHPOINTS.items()
    }


def _target_query_terms(target_command: str) -> list[str]:
    terms = {
        "rewrite": ["rewrite", "rewriting", "gain", "cut", "depth"],
        "resub": ["resub", "resubstitution", "divisor", "gain", "mffc"],
        "refactor": ["refactor", "refactoring", "mffc", "cut", "depth"],
        "orchestrate": [
            "orchestrate",
            "orchestration",
            "rewrite",
            "resub",
            "refactor",
            "balance",
        ],
    }
    return [*terms[target_command], "aig", "equivalence", "deterministic"]


def _deduplicate(values: Sequence[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip().rstrip("/")
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
