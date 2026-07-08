"""Generate the next Flow Agent assignment from reviewed cycle feedback."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

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


CYCLE_RE = re.compile(r"^cycle_(?P<number>\d{3,})$")


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
        "planner_hypothesis": (
            "Use prior build, CEC, QoR delta, and patch feedback to propose a "
            "small FlowTune source-level improvement or a conservative repair."
        ),
        "target_metric": current.get("target_metric", "and_count"),
        "secondary_metrics": current.get(
            "secondary_metrics", ["depth", "runtime", "stability"]
        ),
        "benchmark_scope": current.get("benchmark_scope", ()),
        "allowed_to_read": evidence,
        "recent_evidence": evidence,
        "source_patch_mode": FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        "source_patch_allowed_roots": [FLOWTUNE_SOURCE_SCOPE_PRIMARY],
    }
    return normalize_flow_assignment_scope(assignment)


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
