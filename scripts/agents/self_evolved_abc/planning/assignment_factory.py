"""Build initial coding-agent assignments without CLI/``argparse`` coupling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.agents.self_evolved_abc.benchmarks import (
    DEFAULT_BENCHMARK_SUITE,
    apply_benchmark_suite,
    expand_benchmark_suite,
    promotion_benchmark_count,
    with_abc_native_evaluation_scope,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_SOURCE_TOUCHPOINTS,
    FLOWTUNE_ABCI_SCOPE,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
)
from scripts.agents.self_evolved_abc.flow.multi_flow import (
    default_evaluation_flows,
    default_flow_aggregation,
)
from scripts.agents.self_evolved_abc.flow.promotion import (
    DEFAULT_PROMOTION_THRESHOLDS,
)
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_ABCI_ROOT,
    LOGIC_AGENT_NAME,
    LOGIC_EVALUATION_FLOW_COMMANDS,
    LOGIC_SOURCE_TOUCHPOINTS,
)
from scripts.agents.self_evolved_abc.planning.engine import PlanningEngine
from scripts.agents.self_evolved_abc.roles.registry import (
    get_coding_agent_spec,
    normalize_coding_assignment,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import LEGACY_CYCLE_LAYOUT


def build_initial_assignment(
    *,
    repo_root: Path,
    cycle_id: str,
    previous_cycle_id: str = "cycle_000",
    candidate_id: str = "candidate_001",
    agent_name: str = "flow_agent",
    target_metric: str = "and_count",
    source_patch_mode: str = FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    source_patch_allowed_roots: Sequence[object] = (),
    planner_approved_source_roots: Sequence[object] = (),
    planner_approved_new_source_files: bool = False,
    planner_approved_build_metadata: bool = False,
    benchmarks: Sequence[str] = (),
    benchmark_suite: str = DEFAULT_BENCHMARK_SUITE,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Return one role-consistent first assignment.

    The factory is used by both ``scripts/init_cycle.py`` and the paired
    Planning dispatch, so both entry points receive identical scope and
    benchmark normalisation.
    """

    repo_root = repo_root.resolve()
    spec = get_coding_agent_spec(agent_name)
    explicit_benchmarks = [str(item) for item in benchmarks]
    benchmark_scope = explicit_benchmarks or expand_benchmark_suite(
        repo_root,
        benchmark_suite,
    )
    is_logic = spec.name == LOGIC_AGENT_NAME
    roots = [str(item) for item in source_patch_allowed_roots] or (
        [LOGIC_ABCI_ROOT]
        if is_logic
        else [FLOWTUNE_SOURCE_SCOPE_PRIMARY, FLOWTUNE_ABCI_SCOPE]
    )
    previous = f"experiments/{previous_cycle_id}"

    assignment: dict[str, object] = {
        "agent_name": spec.name,
        "paper_role": spec.paper_role,
        "cycle_id": cycle_id,
        "candidate_id": candidate_id,
        "subsystem": LOGIC_ABCI_ROOT if is_logic else FLOWTUNE_SOURCE_SCOPE_PRIMARY,
        "planner_hypothesis": (
            "Trace one technology-independent rewrite/refactor/resubstitution "
            "decision and propose a narrow CEC-gated candidate."
            if is_logic
            else "Use the previous cycle's QoR and skipped-case evidence to "
            "propose one conservative flow candidate for a small benchmark subset."
        ),
        "target_metric": target_metric,
        "secondary_metrics": ["depth", "runtime", "stability"],
        "promotion_thresholds": DEFAULT_PROMOTION_THRESHOLDS.as_dict(),
        "benchmark_suite": "custom" if explicit_benchmarks else benchmark_suite,
        "benchmark_scope": benchmark_scope,
        "allowed_to_read": [
            f"{previous}/results/summary.csv",
            f"{previous}/results/skipped.csv",
            f"{previous}/results/run_notes.md",
            f"{previous}/outputs",
        ],
        "recent_evidence": [
            f"{previous}/results/summary.csv",
            f"{previous}/results/skipped.csv",
            f"{previous}/results/run_notes.md",
        ],
        "source_patch_mode": source_patch_mode,
        "source_patch_allowed_roots": roots,
        "planner_approved_source_roots": [
            str(item) for item in planner_approved_source_roots
        ],
        "planner_approved_new_source_files": bool(
            planner_approved_new_source_files
        ),
        "planner_approved_build_metadata": bool(
            planner_approved_build_metadata
        ),
        "evaluation_flow_commands": list(
            LOGIC_EVALUATION_FLOW_COMMANDS
            if is_logic
            else DEFAULT_EVAL_FLOW_COMMANDS
        ),
        # The first item sources the candidate-specific recipe.  The remaining
        # recipes are independent structural views, so a source edit is judged
        # by more than one command ordering before a portfolio can promote it.
        "evaluation_flows": default_evaluation_flows(),
        "flow_aggregation": default_flow_aggregation(),
        "repository_context_manifest": "configs/agents/context/repositories.json",
        "repository_context_max_chars": 72000 if is_logic else 60000,
        "repository_context_max_repositories": 9 if is_logic else 6,
        "repository_context_files_per_repository": 3,
        "repository_context_min_available": 9 if is_logic else 6,
        "repository_context_enforce_minimum": True,
    }
    if is_logic:
        assignment["logic_source_touchpoints"] = dict(LOGIC_SOURCE_TOUCHPOINTS)
    else:
        assignment["flow_source_touchpoints"] = dict(FLOW_SOURCE_TOUCHPOINTS)
    if extra_fields:
        assignment.update(dict(extra_fields))

    if explicit_benchmarks:
        assignment = with_abc_native_evaluation_scope(assignment)
    else:
        assignment = apply_benchmark_suite(
            repo_root,
            assignment,
            benchmark_suite,
        )
    assignment = normalize_coding_assignment(assignment)
    return _apply_initial_flow_planning(
        repo_root=repo_root,
        previous_cycle_id=previous_cycle_id,
        assignment=assignment,
    )


def _apply_initial_flow_planning(
    *,
    repo_root: Path,
    previous_cycle_id: str,
    assignment: dict[str, object],
) -> dict[str, object]:
    if assignment.get("agent_name") != "flow_agent":
        return assignment
    engine = PlanningEngine(repo_root)
    result = engine.plan(
        previous_cycle_id,
        benchmark_count=promotion_benchmark_count(assignment) or None,
        candidate_id=str(assignment.get("candidate_id", "candidate_001")),
        artifact_layout=str(
            assignment.get("artifact_layout", LEGACY_CYCLE_LAYOUT)
        ),
    )
    if result is None:
        return assignment
    return normalize_coding_assignment(
        {
            **assignment,
            "previous_cycle_id": previous_cycle_id,
            **engine.next_assignment_updates(result),
        }
    )
