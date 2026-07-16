"""Create the next role-specific coding assignment from reviewed evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.agents.self_evolved_abc.benchmarks import (
    apply_benchmark_suite,
    benchmark_suite_names,
    expand_benchmark_suite,
    promotion_benchmark_count,
    with_abc_native_evaluation_scope,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.assignment import FLOW_CYCLE_DIRS
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    IMPL_CANDIDATE_LABEL,
)
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.planning.engine import PlanningEngine
from scripts.agents.self_evolved_abc.roles.registry import (
    get_coding_agent_spec,
    normalize_coding_assignment,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    CANDIDATE_SCOPED_LAYOUT,
    LEGACY_CYCLE_LAYOUT,
    SUPPORTED_LAYOUTS,
    implementation_root_for,
    validate_candidate_id,
)
from scripts.agents.self_evolved_abc.workflow.failure_evidence import (
    validation_feedback_payload,
)


CYCLE_RE = re.compile(r"^cycle_(?P<number>[0-9]{3,})$")
FLOW_AGENT_NAME = "flow_agent"
LOGIC_AGENT_NAME = "logic_minimization_agent"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a next-cycle coding-agent assignment from review evidence."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--next-cycle", default=None)
    parser.add_argument("--candidate-id", default=None)
    parser.add_argument(
        "--benchmark-suite",
        choices=benchmark_suite_names(),
        default=None,
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    assignment_path = (
        args.assignment
        if args.assignment.is_absolute()
        else repo_root / args.assignment
    )
    context = CycleContext.from_assignment_file(repo_root, assignment_path)
    next_cycle = args.next_cycle or increment_cycle_id(context.cycle_id)
    candidate_id = validate_candidate_id(
        args.candidate_id or context.candidate_id
    )
    assignment = build_next_assignment(
        context,
        next_cycle,
        candidate_id,
        benchmark_suite=args.benchmark_suite,
    )
    path = write_next_assignment(
        context.repo_root,
        next_cycle,
        candidate_id,
        assignment,
        overwrite=args.force,
    )
    print(f"next_assignment: {path}")
    return 0


def build_next_assignment(
    context: CycleContext,
    next_cycle: str,
    candidate_id: str,
    benchmark_suite: str | None = None,
) -> dict[str, object]:
    """Dispatch next-assignment policy by the assignment's registered role."""

    if not CYCLE_RE.fullmatch(next_cycle):
        raise ValueError(f"invalid cycle id: {next_cycle!r}")
    candidate_id = validate_candidate_id(candidate_id)
    spec = get_coding_agent_spec(context.agent_name)
    if context.paper_role != spec.paper_role:
        raise ValueError(
            f"assignment paper_role does not match {spec.name}: "
            f"{context.paper_role!r}"
        )

    current = dict(context.assignment)
    review = _read_previous_review(context)
    benchmark_payload = _next_benchmark_payload(
        context,
        current,
        benchmark_suite,
    )
    common = _base_next_assignment(
        context=context,
        next_cycle=next_cycle,
        candidate_id=candidate_id,
        current=current,
        review=review,
        benchmark_payload=benchmark_payload,
    )
    if spec.name == FLOW_AGENT_NAME:
        assignment = _build_next_flow_assignment(
            context=context,
            current=current,
            common=common,
            benchmark_payload=benchmark_payload,
        )
    elif spec.name == LOGIC_AGENT_NAME:
        assignment = _build_next_logic_assignment(
            context=context,
            current=current,
            common=common,
            review=review,
        )
    else:  # registry currently exposes exactly the two coding roles
        raise ValueError(f"unsupported next-cycle coding role: {spec.name!r}")
    return normalize_coding_assignment(assignment)


def _base_next_assignment(
    *,
    context: CycleContext,
    next_cycle: str,
    candidate_id: str,
    current: Mapping[str, Any],
    review: Mapping[str, Any],
    benchmark_payload: Mapping[str, object],
) -> dict[str, object]:
    evidence = _previous_evidence_paths(context)
    validation_feedback = validation_feedback_payload(context)
    assignment: dict[str, object] = {
        "agent_name": context.agent_name,
        "paper_role": context.paper_role,
        "cycle_id": next_cycle,
        "previous_cycle_id": context.cycle_id,
        "candidate_id": candidate_id,
        "subsystem": current.get("subsystem", ""),
        "previous_review_decision": review.get("decision", "missing"),
        "target_metric": current.get("target_metric", "and_count"),
        "secondary_metrics": list(
            current.get("secondary_metrics", ("depth", "runtime", "stability"))
        ),
        **benchmark_payload,
        "allowed_to_read": list(evidence),
        "recent_evidence": list(evidence),
        "source_patch_mode": current.get(
            "source_patch_mode", FLOW_CANDIDATE_SOURCE_PATCH_DIFF
        ),
        **_carried_role_fields(current),
        **_carried_workflow_metadata(current),
        **_build_champion_payload(context, review),
    }
    if validation_feedback is not None:
        assignment["previous_role_validation_feedback"] = validation_feedback
    return assignment


def _build_next_flow_assignment(
    *,
    context: CycleContext,
    current: Mapping[str, Any],
    common: Mapping[str, object],
    benchmark_payload: Mapping[str, object],
) -> dict[str, object]:
    engine = PlanningEngine(context.repo_root)
    plan_result = engine.plan(
        context.cycle_id,
        benchmark_count=promotion_benchmark_count(benchmark_payload) or None,
        candidate_id=context.candidate_id,
        artifact_layout=str(
            context.assignment.get("artifact_layout", LEGACY_CYCLE_LAYOUT)
        ),
    )
    if plan_result is None:
        raise RuntimeError("PlanningEngine did not return a Flow planning result")
    if plan_result.strategy.should_skip_llm:
        print(
            "\n*** PLANNING ENGINE: recommend skipping this Flow model call. "
            f"Run batch_search targeting `{plan_result.strategy.target_command}` "
            "and feed measured evidence into Planning.\n"
        )
    return {
        **common,
        "evolved_rules": _next_evolved_rules(current, plan_result),
        **engine.next_assignment_updates(plan_result),
    }


def _build_next_logic_assignment(
    *,
    context: CycleContext,
    current: Mapping[str, Any],
    common: Mapping[str, object],
    review: Mapping[str, Any],
) -> dict[str, object]:
    decision = str(review.get("decision", "missing"))
    previous = str(current.get("planner_hypothesis", "")).strip()
    hypothesis = (
        f"Previous Logic candidate {context.candidate_id} finished with "
        f"review decision {decision}. Trace one technology-independent "
        "rewrite, refactor, or resubstitution decision and propose one narrow "
        "source patch that is evaluated under the frozen CEC/QoR contract."
    )
    if previous:
        # Keep one bounded handoff instead of recursively embedding every
        # earlier Planning prompt until the next dispatch exceeds its schema.
        hypothesis += (
            "\n\nPrevious Planning hypothesis (bounded):\n"
            + previous[-2000:]
        )
    return {
        **common,
        "planner_hypothesis": hypothesis,
        "evolved_rules": _next_logic_rules(current, review),
    }


def _previous_evidence_paths(context: CycleContext) -> tuple[str, ...]:
    root = impl_compare_root(context).relative_to(context.repo_root).as_posix()
    shared = f"experiments/{context.cycle_id}/agents"
    return (
        f"experiments/{context.cycle_id}/planning/portfolio_review.json",
        f"{root}/comparison/impl_compare_summary.md",
        f"{root}/comparison/review_decision.json",
        f"{root}/comparison/cec_summary.csv",
        f"{root}/comparison/qor_delta.csv",
        f"{root}/{IMPL_CANDIDATE_LABEL}/patch.diff",
        f"{shared}/feedback/{context.candidate_id}.md",
        f"{shared}/rule_updates/{context.candidate_id}.md",
    )


def _carried_role_fields(current: Mapping[str, Any]) -> dict[str, object]:
    carried: dict[str, object] = {}
    for key in (
        "source_patch_allowed_roots",
        "planner_approved_source_roots",
        "planner_approved_new_source_files",
        "planner_approved_build_metadata",
        "evaluation_flow_commands",
        "diagnostic_flow_commands",
        "promotion_thresholds",
        "flow_source_touchpoints",
        "logic_source_touchpoints",
        "planner_task_type",
    ):
        value = current.get(key)
        if value not in (None, ""):
            carried[key] = value
    return carried


def _carried_workflow_metadata(current: Mapping[str, Any]) -> dict[str, object]:
    carried: dict[str, object] = {}
    for key in (
        "artifact_layout",
        "portfolio_id",
        "planner_dispatch_id",
        "branch_role",
        "evaluation_contract",
        "evaluation_contract_hash",
        "planner_advice_hash",
        "planner_advice_source",
        "baseline_ref",
    ):
        value = current.get(key)
        if value not in (None, ""):
            carried[key] = value
    return carried


def _next_benchmark_payload(
    context: CycleContext,
    current: Mapping[str, Any],
    benchmark_suite: str | None,
) -> dict[str, object]:
    suite = benchmark_suite or str(current.get("benchmark_suite", "")).strip()
    if suite and suite not in ("custom", "explicit"):
        scoped = apply_benchmark_suite(context.repo_root, current, suite)
    elif benchmark_suite:
        scoped = with_abc_native_evaluation_scope(
            {
                "benchmark_suite": benchmark_suite,
                "benchmark_scope": expand_benchmark_suite(
                    context.repo_root,
                    benchmark_suite,
                ),
            }
        )
    else:
        scoped = with_abc_native_evaluation_scope(current)
    return {
        "benchmark_suite": suite or "custom",
        "benchmark_scope": list(scoped.get("benchmark_scope", ())),
        "evaluation_benchmark_scope": list(
            scoped.get("evaluation_benchmark_scope", ())
        ),
        "unsupported_benchmark_scope": list(
            scoped.get("unsupported_benchmark_scope", ())
        ),
        "benchmark_frontend": scoped.get("benchmark_frontend", "abc_native"),
    }


def _build_champion_payload(
    context: CycleContext,
    review: Mapping[str, Any],
) -> dict[str, object]:
    if str(review.get("decision", "")).strip() == "ACCEPT_FOR_NEXT_CYCLE":
        workspace = (
            impl_compare_root(context) / IMPL_CANDIDATE_LABEL / "workspace"
        ).relative_to(context.repo_root).as_posix()
        source_root = f"{workspace}/third_party/FlowTune/src"
        return {
            "baseline_kind": "champion",
            "champion_cycle_id": context.cycle_id,
            "champion_candidate_id": context.candidate_id,
            "champion_source_root": source_root,
            "base_source_root": source_root,
            "champion_abc_bin": f"{source_root}/abc",
            "baseline_abc_bin": f"{source_root}/abc",
        }

    carried: dict[str, object] = {}
    for key in (
        "baseline_kind",
        "champion_cycle_id",
        "champion_candidate_id",
        "champion_source_root",
        "base_source_root",
        "champion_abc_bin",
        "baseline_abc_bin",
    ):
        value = context.assignment.get(key)
        if value not in ("", None):
            carried[key] = value
    if not carried:
        carried["baseline_kind"] = "vanilla"
    return carried


def _read_previous_review(context: CycleContext) -> dict[str, Any]:
    path = impl_compare_root(context) / "comparison" / "review_decision.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _next_evolved_rules(
    current: Mapping[str, Any],
    plan_result: object,
) -> list[str]:
    rules = _clean_rules(current.get("evolved_rules", ()))
    history = getattr(plan_result, "history", ())
    evidence = history[-1] if history else None
    if evidence is not None:
        target = getattr(evidence, "previous_patch_target", "") or "the prior target"
        if getattr(evidence, "is_champion", False):
            rules.append(
                "Preserve the correctness-backed champion mechanism; follow-up "
                "edits must compare against that incumbent."
            )
        elif getattr(evidence, "all_deltas_zero", False):
            rules.append(
                f"The evaluated edit to {target} produced zero AND/depth "
                "movement. Do not repeat it without changed-decision evidence."
            )
        elif int(getattr(evidence, "regressed_benchmark_count", 0)) > 0:
            rules.append(
                f"The evaluated edit to {target} regressed AND QoR. Change the "
                "mechanism or add a general circuit-feature guard."
            )
    return _deduplicate_tail(rules)


def _next_logic_rules(
    current: Mapping[str, Any],
    review: Mapping[str, Any],
) -> list[str]:
    rules = _clean_rules(current.get("evolved_rules", ()))
    decision = str(review.get("decision", ""))
    if decision == "ACCEPT_FOR_NEXT_CYCLE":
        rules.append(
            "Preserve the accepted technology-independent optimization and "
            "keep every follow-up change CEC-backed."
        )
    elif int(review.get("regressed_benchmark_count", 0) or 0) > 0:
        rules.append(
            "Do not repeat the regressing Logic mechanism without a structural "
            "guard that is independent of benchmark identity."
        )
    elif int(review.get("unchanged_benchmark_count", 0) or 0) > 0:
        rules.append(
            "Demonstrate that the targeted Logic decision is reached and changes "
            "the final AIG before repeating a zero-delta patch."
        )
    return _deduplicate_tail(rules)


def _clean_rules(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _deduplicate_tail(rules: Sequence[str]) -> list[str]:
    deduplicated: list[str] = []
    for rule in rules:
        if rule not in deduplicated:
            deduplicated.append(rule)
    return deduplicated[-12:]


def write_next_assignment(
    repo_root: Path,
    cycle_id: str,
    candidate_id: str,
    assignment: dict[str, object],
    *,
    overwrite: bool,
    allow_identical: bool = False,
) -> Path:
    """Persist one normalized assignment without exposing a shared impl lane."""

    repo_root = repo_root.resolve()
    if not CYCLE_RE.fullmatch(cycle_id):
        raise ValueError(f"invalid cycle id: {cycle_id!r}")
    candidate_id = validate_candidate_id(candidate_id)
    if str(assignment.get("cycle_id", "")) != cycle_id:
        raise ValueError("assignment cycle_id does not match output cycle")
    if str(assignment.get("candidate_id", "")) != candidate_id:
        raise ValueError("assignment candidate_id does not match output path")
    normalized = normalize_coding_assignment(assignment)
    if str(normalized.get("cycle_id", "")) != cycle_id:
        raise ValueError("assignment normalizer changed cycle_id")
    if str(normalized.get("candidate_id", "")) != candidate_id:
        raise ValueError("assignment normalizer changed candidate_id")
    layout = str(normalized.get("artifact_layout", LEGACY_CYCLE_LAYOUT)).strip()
    if layout not in SUPPORTED_LAYOUTS:
        raise ValueError(f"unsupported artifact_layout: {layout!r}")

    cycle_dir = repo_root / "experiments" / cycle_id
    for relative in FLOW_CYCLE_DIRS:
        if relative == "impl_compare" and layout == CANDIDATE_SCOPED_LAYOUT:
            continue
        (cycle_dir / relative).mkdir(parents=True, exist_ok=True)
    implementation_root_for(
        repo_root=repo_root,
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        layout=layout,
    ).mkdir(parents=True, exist_ok=True)

    path = cycle_dir / "agents" / "assignments" / f"{candidate_id}.json"
    serialized = json.dumps(normalized, indent=2, sort_keys=True) + "\n"
    if path.exists() and not overwrite:
        if allow_identical and path.read_text(encoding="utf-8") == serialized:
            return path
        raise FileExistsError(f"next assignment already exists: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)
    return path


def increment_cycle_id(cycle_id: str) -> str:
    match = CYCLE_RE.fullmatch(cycle_id)
    if not match:
        raise ValueError(f"invalid cycle id: {cycle_id}")
    width = len(match.group("number"))
    return f"cycle_{int(match.group('number')) + 1:0{width}d}"


if __name__ == "__main__":
    raise SystemExit(main())
