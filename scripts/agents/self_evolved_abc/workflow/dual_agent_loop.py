"""Canonical Planning → (Flow || Logic) → review → Planning loop."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from scripts.agents.self_evolved_abc.benchmarks import (
    DEFAULT_BENCHMARK_SUITE,
    benchmark_suite_names,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.planning.portfolio import (
    BranchDispatch,
    PortfolioPlan,
    load_portfolio_plan,
    portfolio_plan_path,
    validate_portfolio_plan,
)
from scripts.agents.self_evolved_abc.flow.source_patch import (
    source_patch_diff_path,
    source_patch_plan_path,
)
from scripts.agents.self_evolved_abc.model_client import ModelClientError
from scripts.agents.self_evolved_abc.planning_agent import PlanningAgent
from scripts.agents.self_evolved_abc.workflow.artifacts import review_decision_path
from scripts.agents.self_evolved_abc.workflow.branch_run import (
    branch_run_manifest_path,
    load_valid_branch_run,
    write_branch_run_manifest,
)
from scripts.agents.self_evolved_abc.workflow.evaluation_recipe import (
    ensure_evaluation_recipe,
)
from scripts.agents.self_evolved_abc.workflow.portfolio_review import (
    BranchOutcome,
    collect_branch_outcome,
    write_portfolio_review,
)


CommandRunner = Callable[[Sequence[str], Path], int]


@dataclass(frozen=True)
class BranchRun:
    branch: BranchDispatch
    return_code: int | None
    elapsed_seconds: float
    error: str = ""


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Planning Agent rounds that dispatch Flow and Logic Agent "
            "candidates concurrently, then fan in both reviews."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--cycle-id", default="cycle_001")
    parser.add_argument("--previous-cycle", default="cycle_000")
    parser.add_argument("--portfolio-id", default="flow_logic_campaign")
    parser.add_argument("--max-cycles", type=int, default=5)
    parser.add_argument(
        "--max-workers",
        type=int,
        choices=(1, 2),
        default=2,
        help="2 runs Flow and Logic concurrently; 1 is a deterministic fallback.",
    )
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument(
        "--benchmark-suite",
        choices=benchmark_suite_names(),
        default=DEFAULT_BENCHMARK_SUITE,
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--build-timeout-seconds", type=float, default=900.0)
    parser.add_argument(
        "--build-jobs",
        type=int,
        default=4,
        help="Total build-job budget, divided across concurrently running branches.",
    )
    parser.add_argument(
        "--planner-mode",
        choices=("auto", "model", "deterministic"),
        default="auto",
        help=(
            "auto uses the configured Planning model and otherwise a stable "
            "fallback; model fails closed when no provider is configured."
        ),
    )
    parser.add_argument(
        "--build-candidate-binary",
        action="store_true",
        help="Build each candidate's isolated ABC binary before CEC/QoR.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Write the frozen Planning dispatch and two assignments, then stop.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.max_cycles < 1:
        print("dual_agent_loop: --max-cycles must be >= 1", file=sys.stderr)
        return 2
    if args.timeout_seconds <= 0 or args.build_timeout_seconds <= 0:
        print("dual_agent_loop: timeouts must be > 0", file=sys.stderr)
        return 2
    if args.build_jobs < 1:
        print("dual_agent_loop: --build-jobs must be >= 1", file=sys.stderr)
        return 2
    repo_root = args.repo_root.resolve()
    lock = _acquire_campaign_lock(repo_root, args.portfolio_id)
    if lock is None:
        print(
            "dual_agent_loop: another process owns this portfolio campaign",
            file=sys.stderr,
        )
        return 3
    try:
        try:
            return _run_campaign(repo_root, args)
        except (OSError, ValueError, ModelClientError) as exc:
            print(f"dual_agent_loop: {exc}", file=sys.stderr)
            return 2
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def _run_campaign(repo_root: Path, args: argparse.Namespace) -> int:
    current_plan = _load_or_create_initial_plan(repo_root, args)
    _print_plan(current_plan, repo_root)
    if args.prepare_only:
        return 0

    had_failed_branch = False
    for iteration in range(1, args.max_cycles + 1):
        print("\n" + "=" * 72)
        print(
            "dual_agent_loop: "
            f"round {iteration}/{args.max_cycles} ({current_plan.cycle_id})"
        )
        print("=" * 72 + "\n")
        outcomes = execute_portfolio_plan(
            repo_root=repo_root,
            plan=current_plan,
            max_workers=args.max_workers,
            timeout_seconds=args.timeout_seconds,
            build_timeout_seconds=args.build_timeout_seconds,
            build_jobs=args.build_jobs,
            build_candidate_binary=args.build_candidate_binary,
        )
        review = write_portfolio_review(
            repo_root=repo_root,
            plan=current_plan,
            outcomes=outcomes,
        )
        had_failed_branch = had_failed_branch or any(
            item.status == "failed"
            or item.return_code != 0
            or bool(item.error)
            for item in outcomes
        )
        print(
            "dual_agent_loop: fan-in "
            f"status={review['round_status']} "
            f"winner={review['selected_candidate_id'] or 'none'}"
        )

        if not bool(review["quorum_reached"]):
            print(
                "dual_agent_loop: stopping — both branch reviews are required "
                "before the next Planning round"
            )
            break
        if iteration == args.max_cycles:
            print("dual_agent_loop: stopping — reached --max-cycles")
            break

        next_cycle = _increment_cycle_id(current_plan.cycle_id)
        next_plan_path = portfolio_plan_path(repo_root, next_cycle)
        if next_plan_path.is_file():
            current_plan = load_portfolio_plan(repo_root, next_cycle)
        else:
            current_plan = PlanningAgent.create_next_parallel_coding_dispatch(
                repo_root=repo_root,
                current_plan=current_plan,
                portfolio_review=review,
                next_cycle_id=next_cycle,
                timeout_seconds=args.timeout_seconds,
                build_timeout_seconds=args.build_timeout_seconds,
                planner_mode=args.planner_mode,
            )
            assert isinstance(current_plan, PortfolioPlan)
        _print_plan(current_plan, repo_root)

    return 1 if had_failed_branch else 0


def _acquire_campaign_lock(repo_root: Path, portfolio_id: str):
    lock_root = repo_root / "experiments" / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        character
        for character in str(portfolio_id)
        if character.isalnum() or character in ("_", "-")
    )
    if not safe_name or safe_name != str(portfolio_id):
        raise ValueError(f"invalid portfolio id: {portfolio_id!r}")
    handle = (lock_root / f"{safe_name}.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def execute_portfolio_plan(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    max_workers: int = 2,
    timeout_seconds: float = 300.0,
    build_timeout_seconds: float = 900.0,
    build_jobs: int = 4,
    build_candidate_binary: bool = False,
    command_runner: CommandRunner | None = None,
) -> tuple[BranchOutcome, ...]:
    """Run both branches all-settled; one failure never cancels its sibling."""

    if max_workers not in (1, 2):
        raise ValueError("max_workers must be 1 or 2")
    if timeout_seconds <= 0 or build_timeout_seconds <= 0:
        raise ValueError("timeouts must be > 0")
    if build_jobs < 1:
        raise ValueError("build_jobs must be >= 1")
    repo_root = repo_root.resolve()
    validate_portfolio_plan(plan, repo_root=repo_root)
    contract_timeout = float(plan.evaluation_contract["timeout_seconds"])
    contract_build_timeout = float(
        plan.evaluation_contract["build_timeout_seconds"]
    )
    if float(timeout_seconds) != contract_timeout:
        raise ValueError("runtime timeout diverges from frozen evaluation contract")
    if float(build_timeout_seconds) != contract_build_timeout:
        raise ValueError("build timeout diverges from frozen evaluation contract")
    runner = command_runner or _default_command_runner
    per_branch_build_jobs = max(1, build_jobs // max_workers)
    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="abc-coding-agent",
    ) as executor:
        futures = {
            executor.submit(
                _execute_branch_lifecycle,
                repo_root=repo_root,
                plan=plan,
                branch=branch,
                command=_build_candidate_command(
                    repo_root=repo_root,
                    branch=branch,
                    timeout_seconds=timeout_seconds,
                    build_timeout_seconds=build_timeout_seconds,
                    build_jobs=per_branch_build_jobs,
                    build_candidate_binary=build_candidate_binary,
                ),
                command_runner=runner,
            ): branch
            for branch in plan.branches
        }
        outcomes_by_role: dict[str, BranchOutcome] = {}
        for future in as_completed(futures):
            branch = futures[future]
            try:
                outcomes_by_role[branch.branch_role] = future.result()
            except BaseException as exc:  # all-settled boundary
                outcomes_by_role[branch.branch_role] = _failed_branch_outcome(
                    repo_root=repo_root,
                    plan=plan,
                    branch=branch,
                    error=f"branch lifecycle raised {type(exc).__name__}: {exc}",
                )
    return tuple(outcomes_by_role[branch.branch_role] for branch in plan.branches)


def _execute_branch_lifecycle(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    branch: BranchDispatch,
    command: Sequence[str],
    command_runner: CommandRunner,
) -> BranchOutcome:
    """Preflight, run, collect, and checkpoint one independently settled lane."""

    context = CycleContext.from_assignment_file(repo_root, branch.assignment_path)
    review_path = review_decision_path(context)
    resumed = load_valid_branch_run(
        repo_root=repo_root,
        plan=plan,
        branch=branch,
        review_path=review_path,
    )
    if resumed is not None:
        return collect_branch_outcome(
            repo_root=repo_root,
            branch=branch,
            return_code=_optional_manifest_int(resumed.get("return_code")),
            elapsed_seconds=float(resumed.get("elapsed_seconds", 0.0)),
            runner_error=str(resumed.get("error", "")),
        )

    _reset_branch_outputs(repo_root, plan, branch, context)
    ensure_evaluation_recipe(repo_root, branch.assignment_path)
    run = _run_branch(
        repo_root=repo_root,
        branch=branch,
        command=command,
        command_runner=command_runner,
    )
    outcome = collect_branch_outcome(
        repo_root=repo_root,
        branch=branch,
        return_code=run.return_code,
        elapsed_seconds=run.elapsed_seconds,
        runner_error=run.error,
    )
    write_branch_run_manifest(
        repo_root=repo_root,
        plan=plan,
        branch=branch,
        review_path=review_path,
        return_code=run.return_code,
        elapsed_seconds=run.elapsed_seconds,
        error=outcome.error,
        status=outcome.status,
    )
    return outcome


def _reset_branch_outputs(
    repo_root: Path,
    plan: PortfolioPlan,
    branch: BranchDispatch,
    context: CycleContext,
) -> None:
    """Remove stale candidate evidence before a non-resumed execution."""

    implementation_root = review_decision_path(context).parent.parent
    if implementation_root.exists():
        shutil.rmtree(implementation_root)
    for path in (
        context.artifact_paths().plan,
        context.artifact_paths().candidate_change,
        context.artifact_paths().feedback,
        context.artifact_paths().rule_update,
        source_patch_plan_path(context),
        source_patch_diff_path(context),
        branch_run_manifest_path(repo_root, plan, branch),
    ):
        path.unlink(missing_ok=True)


def _failed_branch_outcome(
    *,
    repo_root: Path,
    plan: PortfolioPlan,
    branch: BranchDispatch,
    error: str,
) -> BranchOutcome:
    artifact_root = (
        Path("experiments")
        / plan.cycle_id
        / "candidates"
        / branch.candidate_id
        / "impl_compare"
    )
    return BranchOutcome(
        branch_role=branch.branch_role,
        agent_name=branch.agent_name,
        candidate_id=branch.candidate_id,
        status="failed",
        return_code=None,
        decision="MISSING_REVIEW",
        eligible_for_promotion=False,
        artifact_root=artifact_root.as_posix(),
        review_path=(artifact_root / "comparison" / "review_decision.json").as_posix(),
        elapsed_seconds=0.0,
        error=error,
        expected_benchmark_count=len(plan.evaluation_contract.get("benchmark_scope", ())),
    )


def _optional_manifest_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _run_branch(
    *,
    repo_root: Path,
    branch: BranchDispatch,
    command: Sequence[str],
    command_runner: CommandRunner,
) -> BranchRun:
    start = time.monotonic()
    try:
        return_code = int(command_runner(command, repo_root))
        error = "" if return_code == 0 else f"pipeline return code {return_code}"
    except BaseException as exc:  # converted into an all-settled lane result
        return_code = None
        error = f"runner raised {type(exc).__name__}: {exc}"
    return BranchRun(
        branch=branch,
        return_code=return_code,
        elapsed_seconds=time.monotonic() - start,
        error=error,
    )


def _build_candidate_command(
    *,
    repo_root: Path,
    branch: BranchDispatch,
    timeout_seconds: float,
    build_timeout_seconds: float,
    build_jobs: int,
    build_candidate_binary: bool,
) -> tuple[str, ...]:
    command = [
        sys.executable,
        "-B",
        "-m",
        "scripts.agents.self_evolved_abc.workflow.candidate_pipeline",
        "--repo-root",
        str(repo_root),
        "--assignment",
        str(branch.assignment_path.relative_to(repo_root)),
        "--skip-next-cycle",
        "--timeout-seconds",
        f"{timeout_seconds:g}",
        "--build-timeout-seconds",
        f"{build_timeout_seconds:g}",
        "--build-jobs",
        str(build_jobs),
    ]
    if build_candidate_binary:
        command.append("--build-candidate-binary")
    return tuple(command)


def _default_command_runner(command: Sequence[str], cwd: Path) -> int:
    print(f"running: {' '.join(command)}")
    return subprocess.run(command, cwd=cwd, check=False).returncode


def _load_or_create_initial_plan(
    repo_root: Path,
    args: argparse.Namespace,
) -> PortfolioPlan:
    path = portfolio_plan_path(repo_root, args.cycle_id)
    if path.is_file():
        plan = load_portfolio_plan(repo_root, args.cycle_id)
        print(f"dual_agent_loop: resumed {path.relative_to(repo_root)}")
        return plan
    plan = PlanningAgent.create_parallel_coding_dispatch(
        repo_root=repo_root,
        cycle_id=args.cycle_id,
        previous_cycle_id=args.previous_cycle,
        portfolio_id=args.portfolio_id,
        benchmark_suite=args.benchmark_suite,
        benchmarks=args.benchmark,
        timeout_seconds=args.timeout_seconds,
        build_timeout_seconds=args.build_timeout_seconds,
        planner_mode=args.planner_mode,
    )
    assert isinstance(plan, PortfolioPlan)
    return plan


def _print_plan(plan: PortfolioPlan, repo_root: Path) -> None:
    print(
        "dual_agent_loop: Planning dispatch = "
        f"{portfolio_plan_path(repo_root, plan.cycle_id).relative_to(repo_root)}"
    )
    for branch in plan.branches:
        print(
            f"  {branch.branch_role}: {branch.agent_name} → "
            f"{branch.assignment_path.relative_to(repo_root)}"
        )


def _increment_cycle_id(cycle_id: str) -> str:
    prefix, separator, number = cycle_id.rpartition("_")
    if not separator or not number.isdigit():
        raise ValueError(f"invalid cycle id: {cycle_id!r}")
    return f"{prefix}_{int(number) + 1:0{len(number)}d}"


if __name__ == "__main__":
    raise SystemExit(main())
