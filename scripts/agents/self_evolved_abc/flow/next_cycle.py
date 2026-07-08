"""Generate the next Flow Agent assignment from reviewed cycle feedback."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.assignment import (
    FLOW_CYCLE_DIRS,
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_SOURCE_TOUCHPOINTS,
    FLOWTUNE_ABCI_SCOPE,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
    IMPL_CANDIDATE_LABEL,
)


CYCLE_RE = re.compile(r"^cycle_(?P<number>\d{3,})$")
GATE_REPAIR_DECISIONS = frozenset(
    (
        "REPAIR_PATCH",
        "REPAIR_VALIDATION",
        "REPAIR_SMOKE",
        "REPAIR_COMPILE",
        "REPAIR_EVALUATION",
    )
)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a next-cycle Flow Agent assignment from review evidence."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--next-cycle", default=None)
    parser.add_argument("--candidate-id", default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    context = CycleContext.from_assignment_file(args.repo_root.resolve(), args.assignment)
    next_cycle = args.next_cycle or increment_cycle_id(context.cycle_id)
    candidate_id = args.candidate_id or context.candidate_id
    assignment = build_next_assignment(context, next_cycle, candidate_id)
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
) -> dict[str, object]:
    previous_base = f"experiments/{context.cycle_id}"
    current = dict(context.assignment)
    review = _read_previous_review(context)
    evidence = [
        f"{previous_base}/impl_compare/comparison/impl_compare_summary.md",
        f"{previous_base}/impl_compare/comparison/review_decision.json",
        f"{previous_base}/impl_compare/comparison/cec_summary.csv",
        f"{previous_base}/impl_compare/comparison/qor_delta.csv",
        f"{previous_base}/impl_compare/{IMPL_CANDIDATE_LABEL}/patch.diff",
        f"{previous_base}/agents/feedback/{context.candidate_id}.md",
        f"{previous_base}/agents/rule_updates/{context.candidate_id}.md",
    ]
    assignment = {
        "agent_name": current.get("agent_name", "flow_agent"),
        "paper_role": current.get("paper_role", "Flow Agent"),
        "cycle_id": next_cycle,
        "previous_cycle_id": context.cycle_id,
        "candidate_id": candidate_id,
        "subsystem": FLOWTUNE_SOURCE_SCOPE_PRIMARY,
        "previous_review_decision": review.get("decision", "missing"),
        "planner_hypothesis": _build_planner_hypothesis(context, review),
        "target_metric": current.get("target_metric", "and_count"),
        "secondary_metrics": current.get(
            "secondary_metrics", ["depth", "runtime", "stability"]
        ),
        "benchmark_scope": current.get("benchmark_scope", ()),
        "allowed_to_read": evidence,
        "recent_evidence": evidence,
        "source_patch_mode": FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        "source_patch_allowed_roots": [
            FLOWTUNE_SOURCE_SCOPE_PRIMARY,
            FLOWTUNE_ABCI_SCOPE,
        ],
        "evaluation_flow_commands": list(DEFAULT_EVAL_FLOW_COMMANDS),
        "flow_source_touchpoints": dict(FLOW_SOURCE_TOUCHPOINTS),
    }
    return normalize_flow_assignment_scope(assignment)


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


def _build_planner_hypothesis(
    context: CycleContext,
    review: dict[str, Any],
) -> str:
    decision = str(review.get("decision", "missing")).strip() or "missing"
    reason = str(review.get("reason", "")).strip()
    next_action = str(review.get("next_action", "")).strip()
    avg_and = _format_optional_float(review.get("average_and_improve_pct"))

    lines = [
        f"Previous cycle {context.cycle_id} review decision: {decision}.",
    ]
    if reason:
        lines.append(f"Review reason: {reason}")
    if avg_and:
        lines.append(f"Average AND improvement pct: {avg_and}.")
    if next_action:
        lines.append(f"Review next action: {next_action}")

    if decision == "REPAIR_QOR":
        lines.extend(
            (
                "The previous candidate passed build and CEC but did not improve "
                "the target QoR metric, so this is a targeted QoR repair cycle.",
                "Read the previous qor_delta.csv and patch.diff first. Do not "
                "repeat the same patch verbatim. Propose a smaller or alternative "
                "FlowTune source_patch_diff that preserves correctness while "
                "addressing the measured AND/depth/runtime deltas.",
                "If all AND/depth deltas were zero, treat it as a reachability "
                "signal: prefer a source target or strategy that is likely to be "
                "exercised by the evaluation flow, but do not force a subsystem "
                "switch solely because one exploratory patch had no effect.",
            )
        )
    elif decision == "REJECT_CEC":
        lines.extend(
            (
                "The previous candidate failed correctness. Treat its QoR as "
                "invalid and propose only a semantic repair or rollback-oriented "
                "source_patch_diff.",
            )
        )
    elif decision in GATE_REPAIR_DECISIONS:
        lines.extend(
            (
                "Repair the specific failed gate before attempting a new "
                "optimization. Keep the original source-patch scope and preserve "
                "the paper gate order.",
            )
        )
    elif decision == "ACCEPT_FOR_NEXT_CYCLE":
        lines.extend(
            (
                "Use the accepted candidate as positive evidence, but keep the "
                "next change small and independently attributable.",
            )
        )
    else:
        lines.extend(
            (
                "Use prior build, CEC, QoR delta, and patch feedback to propose "
                "a small FlowTune source-level improvement or a conservative "
                "repair.",
            )
        )

    return "\n".join(lines)


def _format_optional_float(value: object) -> str:
    if value in ("", None):
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


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
