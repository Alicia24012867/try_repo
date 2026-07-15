#!/usr/bin/env python3
"""Create one role-scoped coding-agent cycle assignment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.agents.self_evolved_abc.benchmarks import (
    DEFAULT_BENCHMARK_SUITE,
    benchmark_suite_names,
)
from scripts.agents.self_evolved_abc.flow.assignment import FLOW_CYCLE_DIRS
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_ABC_FLOW,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_CANDIDATE_SOURCE_PATCH_TODO,
)
from scripts.agents.self_evolved_abc.planning.assignment_factory import (
    build_initial_assignment,
)
from scripts.agents.self_evolved_abc.roles.registry import (
    coding_agent_names,
    get_coding_agent_spec,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    CANDIDATE_SCOPED_LAYOUT,
    implementation_root_for,
    validate_candidate_id,
)


CYCLE_RE = re.compile(r"^cycle_[0-9]{3,}$")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize a candidate-scoped coding-agent cycle."
    )
    parser.add_argument("cycle_id", help="Cycle id such as cycle_001.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--previous-cycle", default="cycle_000")
    parser.add_argument(
        "--candidate-id",
        default=None,
        help="Defaults to the registered role's candidate prefix plus _001.",
    )
    parser.add_argument(
        "--agent-name",
        choices=coding_agent_names(),
        default="flow_agent",
    )
    parser.add_argument("--target-metric", default="and_count")
    parser.add_argument(
        "--source-patch-mode",
        default=FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        choices=(
            FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
            FLOW_CANDIDATE_ABC_FLOW,
            FLOW_CANDIDATE_SOURCE_PATCH_TODO,
        ),
    )
    parser.add_argument(
        "--source-patch-allowed-root",
        dest="source_patch_allowed_roots",
        action="append",
        default=[],
        help="Repository path the coding agent may patch. Repeatable.",
    )
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument(
        "--benchmark-suite",
        choices=benchmark_suite_names(),
        default=DEFAULT_BENCHMARK_SUITE,
    )
    parser.add_argument("--no-assignment", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def validate_cycle_id(value: str) -> str:
    cycle_id = str(value).strip()
    if not CYCLE_RE.fullmatch(cycle_id):
        raise ValueError(
            f"invalid cycle id: {value!r}; expected cycle_001 style"
        )
    return cycle_id


def create_cycle_dirs(
    repo_root: Path,
    *,
    cycle_id: str,
    candidate_id: str,
) -> None:
    """Create shared cycle directories and one isolated candidate lane."""

    cycle_dir = repo_root / "experiments" / cycle_id
    for relative in FLOW_CYCLE_DIRS:
        if relative == "impl_compare":
            continue
        (cycle_dir / relative).mkdir(parents=True, exist_ok=True)
    implementation_root_for(
        repo_root=repo_root,
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        layout=CANDIDATE_SCOPED_LAYOUT,
    ).mkdir(parents=True, exist_ok=True)


def build_assignment(args: argparse.Namespace) -> dict[str, object]:
    return build_initial_assignment(
        repo_root=args.repo_root.resolve(),
        cycle_id=args.cycle_id,
        previous_cycle_id=args.previous_cycle,
        candidate_id=args.candidate_id,
        agent_name=args.agent_name,
        target_metric=args.target_metric,
        source_patch_mode=args.source_patch_mode,
        source_patch_allowed_roots=args.source_patch_allowed_roots,
        benchmarks=args.benchmark,
        benchmark_suite=args.benchmark_suite,
        extra_fields={"artifact_layout": CANDIDATE_SCOPED_LAYOUT},
    )


def write_json(
    path: Path,
    payload: dict[str, object],
    *,
    overwrite: bool,
) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists() and not overwrite:
        if path.read_text(encoding="utf-8") == serialized:
            return
        raise FileExistsError(f"assignment already exists: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.repo_root = args.repo_root.resolve()
    args.cycle_id = validate_cycle_id(args.cycle_id)
    args.previous_cycle = validate_cycle_id(args.previous_cycle)
    spec = get_coding_agent_spec(args.agent_name)
    args.candidate_id = validate_candidate_id(
        args.candidate_id or f"{spec.candidate_prefix}_001"
    )
    create_cycle_dirs(
        args.repo_root,
        cycle_id=args.cycle_id,
        candidate_id=args.candidate_id,
    )

    if not args.no_assignment:
        assignment = build_assignment(args)
        path = (
            args.repo_root
            / "experiments"
            / args.cycle_id
            / "agents"
            / "assignments"
            / f"{args.candidate_id}.json"
        )
        write_json(path, assignment, overwrite=args.force)

    print(f"initialized: {args.repo_root / 'experiments' / args.cycle_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
