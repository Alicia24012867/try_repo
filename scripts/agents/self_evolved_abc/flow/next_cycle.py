"""Generate the next Flow Agent assignment from reviewed cycle feedback.

Uses the deterministic PlanningEngine to select strategy, target command,
and adaptive thresholds.  The engine overrides hard-coded planner logic
when cycle evidence is available.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from scripts.agents.self_evolved_abc.benchmarks import (
    apply_benchmark_suite,
    benchmark_suite_names,
    expand_benchmark_suite,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.assignment import (
    FLOW_CYCLE_DIRS,
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
    IMPL_CANDIDATE_LABEL,
)
from scripts.agents.self_evolved_abc.planning.engine import PlanningEngine


CYCLE_RE = re.compile(r"^cycle_(?P<number>\d{3,})$")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a next-cycle Flow Agent assignment from review evidence."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--next-cycle", default=None)
    parser.add_argument("--candidate-id", default=None)
    parser.add_argument(
        "--benchmark-suite",
        choices=benchmark_suite_names(),
        default=None,
        help=(
            "Override the next assignment benchmark scope. "
            "Use large_70 to evaluate the full local benchmark sample."
        ),
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    context = CycleContext.from_assignment_file(args.repo_root.resolve(), args.assignment)
    next_cycle = args.next_cycle or increment_cycle_id(context.cycle_id)
    candidate_id = args.candidate_id or context.candidate_id
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
    previous_base = f"experiments/{context.cycle_id}"
    current = dict(context.assignment)
    review = _read_previous_review(context)
    champion = _build_champion_payload(context, review)
    evidence = [
        f"{previous_base}/impl_compare/comparison/impl_compare_summary.md",
        f"{previous_base}/impl_compare/comparison/review_decision.json",
        f"{previous_base}/impl_compare/comparison/cec_summary.csv",
        f"{previous_base}/impl_compare/comparison/qor_delta.csv",
        f"{previous_base}/impl_compare/{IMPL_CANDIDATE_LABEL}/patch.diff",
        f"{previous_base}/agents/feedback/{context.candidate_id}.md",
        f"{previous_base}/agents/rule_updates/{context.candidate_id}.md",
    ]
    benchmark_scope, benchmark_suite_name = _next_benchmark_scope(
        context,
        current,
        benchmark_suite,
    )

    # --- Planning engine integration (always active) ---
    engine = PlanningEngine(context.repo_root)
    plan_result = engine.plan(
        context.cycle_id,
        benchmark_count=len(benchmark_scope) or None,
    )
    # Engine always returns a result — uses default strategy when no evidence.
    assert plan_result is not None, "PlanningEngine.plan() must not return None"
    planning_updates = engine.next_assignment_updates(plan_result)
    if plan_result.strategy.should_skip_llm:
        print(
            f"\n*** PLANNING ENGINE: recommend skipping LLM cycle {next_cycle}. "
            f"Run batch_search targeting `{plan_result.strategy.target_command}` "
            f"instead.\n"
        )

    assignment = {
        "agent_name": current.get("agent_name", "flow_agent"),
        "paper_role": current.get("paper_role", "Flow Agent"),
        "cycle_id": next_cycle,
        "previous_cycle_id": context.cycle_id,
        "candidate_id": candidate_id,
        "subsystem": FLOWTUNE_SOURCE_SCOPE_PRIMARY,
        "previous_review_decision": review.get("decision", "missing"),
        "target_metric": current.get("target_metric", "and_count"),
        "secondary_metrics": current.get(
            "secondary_metrics", ["depth", "runtime", "stability"]
        ),
        "benchmark_suite": benchmark_suite_name,
        "benchmark_scope": benchmark_scope,
        "allowed_to_read": evidence,
        "recent_evidence": evidence,
        "source_patch_mode": FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        **planning_updates,
        **champion,
    }
    return normalize_flow_assignment_scope(assignment)


def _next_benchmark_scope(
    context: CycleContext,
    current: dict[str, Any],
    benchmark_suite: str | None,
) -> tuple[list[str], str]:
    suite = benchmark_suite or str(current.get("benchmark_suite", "")).strip()
    if suite and suite not in ("custom", "explicit"):
        scoped = apply_benchmark_suite(context.repo_root, current, suite)
        return list(scoped.get("benchmark_scope", ())), suite
    if benchmark_suite:
        return expand_benchmark_suite(context.repo_root, benchmark_suite), benchmark_suite
    return list(current.get("benchmark_scope", ())), suite or "custom"


def _build_champion_payload(
    context: CycleContext,
    review: dict[str, Any],
) -> dict[str, object]:
    """Return next-cycle baseline/champion fields.

    Accepted candidates become the next cycle's source and binary baseline.
    Non-promoted cycles keep the incoming champion, if one already exists.
    """

    if str(review.get("decision", "")).strip() == "ACCEPT_FOR_NEXT_CYCLE":
        workspace = (
            f"experiments/{context.cycle_id}/impl_compare/"
            f"{IMPL_CANDIDATE_LABEL}/workspace"
        )
        source_root = f"{workspace}/third_party/FlowTune/src"
        abc_bin = f"{source_root}/abc"
        return {
            "baseline_kind": "champion",
            "champion_cycle_id": context.cycle_id,
            "champion_candidate_id": context.candidate_id,
            "champion_source_root": source_root,
            "base_source_root": source_root,
            "champion_abc_bin": abc_bin,
            "baseline_abc_bin": abc_bin,
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
    path = (
        context.repo_root
        / "experiments"
        / context.cycle_id
        / "impl_compare"
        / "comparison"
        / "review_decision.json"
    )
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_next_assignment(
    repo_root: Path,
    cycle_id: str,
    candidate_id: str,
    assignment: dict[str, object],
    *,
    overwrite: bool,
) -> Path:
    cycle_dir = repo_root / "experiments" / cycle_id
    for relative in FLOW_CYCLE_DIRS:
        (cycle_dir / relative).mkdir(parents=True, exist_ok=True)
    path = cycle_dir / "agents" / "assignments" / f"{candidate_id}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(f"next assignment already exists: {path}")
    path.write_text(json.dumps(assignment, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def increment_cycle_id(cycle_id: str) -> str:
    match = CYCLE_RE.match(cycle_id)
    if not match:
        raise ValueError(f"invalid cycle id: {cycle_id}")
    width = len(match.group("number"))
    return f"cycle_{int(match.group('number')) + 1:0{width}d}"


if __name__ == "__main__":
    raise SystemExit(main())
