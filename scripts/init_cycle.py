#!/usr/bin/env python3
"""Initialize one experiment cycle skeleton.

Without --benchmark flags, the assignment expands to all .blif designs
under benchmarks/epfl/, benchmarks/iscas85/, and benchmarks/iscas89/
(30 designs total).

Example with explicit scope:
    python3 -B scripts/init_cycle.py cycle_001 \\
        --previous-cycle cycle_000 \\
        --candidate-id candidate_001 \\
        --agent-name flow_agent \\
        --benchmark benchmarks/epfl/epfl_adder.blif \\
        --benchmark benchmarks/epfl/epfl_bar.blif
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from scripts.agents.self_evolved_abc.flow.assignment import (
    FLOW_CYCLE_DIRS,
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.flow.promotion import (
    DEFAULT_PROMOTION_THRESHOLDS,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_SOURCE_TOUCHPOINTS,
    FLOWTUNE_ABCI_SCOPE,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
)

CYCLE_RE = re.compile(r"^cycle_\d{3,}$")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize an experiment cycle.")
    parser.add_argument("cycle_id", help="Cycle id such as cycle_001.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--previous-cycle", default="cycle_000")
    parser.add_argument("--candidate-id", default="candidate_001")
    parser.add_argument("--agent-name", default="flow_agent")
    parser.add_argument("--paper-role", default="Flow Agent")
    parser.add_argument("--subsystem", default="configs/flows")
    parser.add_argument("--target-metric", default="and_count")
    parser.add_argument(
        "--source-patch-mode",
        default="source_patch_diff",
        choices=("source_patch_diff", "abc_flow", "source_patch_todo"),
        help="Which candidate kind the Flow Agent should produce.",
    )
    parser.add_argument(
        "--source-patch-allowed-root",
        dest="source_patch_allowed_roots",
        action="append",
        default=[],
        help="Repository paths the model is allowed to patch. Repeatable.",
    )
    parser.add_argument("--with-assignment", action="store_true", default=True)
    parser.add_argument("--no-assignment", dest="with_assignment", action="store_false")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--benchmark", action="append", default=())
    return parser.parse_args()

def validate_cycle_id(value: str) -> str:
    if not CYCLE_RE.match(value):
        raise ValueError(f"invalid cycle id: {value!r}; expected cycle_001 style")
    return value

def create_cycle_dirs(cycle_dir: Path) -> None:
    for relative in FLOW_CYCLE_DIRS:
        path = cycle_dir / relative
        path.mkdir(parents=True, exist_ok=True)
        gitkeep = path / ".gitkeep"
        gitkeep.touch(exist_ok=True)
        
def build_assignment(args: argparse.Namespace) -> dict[str, object]:
    previous = f"experiments/{args.previous_cycle}"
    benchmarks = list(args.benchmark) or _default_benchmarks(args.repo_root)


def _default_benchmarks(repo_root: Path) -> list[str]:
    """Expand EPFL + ISCAS85 + ISCAS89 .blif globs into a sorted scope."""
    patterns = (
        "benchmarks/epfl/*.blif",
        "benchmarks/iscas85/*.blif",
        "benchmarks/iscas89/*.blif",
    )
    result: list[str] = []
    for pattern in patterns:
        for path in sorted(repo_root.glob(pattern)):
            result.append(str(path.relative_to(repo_root)))
    return result


def build_assignment(args: argparse.Namespace) -> dict[str, object]:
    previous = f"experiments/{args.previous_cycle}"
    benchmarks = list(args.benchmark) or _default_benchmarks(args.repo_root)
    source_patch_roots = list(args.source_patch_allowed_roots) or [
        FLOWTUNE_SOURCE_SCOPE_PRIMARY,
        FLOWTUNE_ABCI_SCOPE,
    ]

    assignment = {
        "agent_name": args.agent_name,
        "paper_role": args.paper_role,
        "cycle_id": args.cycle_id,
        "candidate_id": args.candidate_id,
        "subsystem": args.subsystem,
        "planner_hypothesis": (
            "Use the previous cycle's QoR and skipped-case evidence to propose "
            "one conservative flow candidate for a small benchmark subset."
        ),
        "target_metric": args.target_metric,
        "secondary_metrics": ["depth", "runtime", "stability"],
        "promotion_thresholds": DEFAULT_PROMOTION_THRESHOLDS.as_dict(),
        "benchmark_scope": benchmarks,
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
        "source_patch_mode": args.source_patch_mode,
        "source_patch_allowed_roots": source_patch_roots,
        "evaluation_flow_commands": list(DEFAULT_EVAL_FLOW_COMMANDS),
        "flow_source_touchpoints": dict(FLOW_SOURCE_TOUCHPOINTS),
    }
    return normalize_flow_assignment_scope(assignment)
    
def write_json(path: Path, payload: dict[str, object], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"assignment already exists: {path}")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    cycle_id = validate_cycle_id(args.cycle_id)
    cycle_dir = repo_root / "experiments" / cycle_id

    create_cycle_dirs(cycle_dir)

    if args.with_assignment:
        assignment = build_assignment(args)
        assignment_path = (
            cycle_dir / "agents" / "assignments" / f"{args.candidate_id}.json"
        )
        write_json(assignment_path, assignment, overwrite=args.force)

    print(f"initialized: {cycle_dir}")
    return 0
    
if __name__ == "__main__":
    raise SystemExit(main())
