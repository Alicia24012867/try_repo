"""Canonical Planning → (Flow || Logic) → review → Planning loop."""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import shutil
import subprocess
import sys
import time
import traceback
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
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    review_decision_path,
    safe_repo_path,
    validate_candidate_id,
    validate_portfolio_cycle_id,
)
from scripts.agents.self_evolved_abc.workflow.branch_run import (
    branch_run_manifest_path,
    load_valid_branch_run,
    write_branch_run_manifest,
)
from scripts.agents.self_evolved_abc.workflow.failure_status import (
    is_coding_infrastructure_failure_status,
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
    lock = _acquire_campaign_lock(repo_root)
    if lock is None:
        print(
            "dual_agent_loop: another process owns this repository campaign",
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
        _print_branch_outcomes(
            repo_root=repo_root,
            cycle_id=current_plan.cycle_id,
            outcomes=outcomes,
        )
        review = write_portfolio_review(
            repo_root=repo_root,
            plan=current_plan,
            outcomes=outcomes,
        )
        # A review command deliberately returns one for a valid negative
        # decision such as REPAIR_QOR.  That branch is settled; malformed or
        # missing reviews and unexpected runner exits remain execution failures.
        had_failed_branch = had_failed_branch or any(
            _branch_execution_failed(item) for item in outcomes
        )
        reviewed_count = int(review["reviewed_count"])
        failed_count = int(review["failed_count"])
        print(
            "dual_agent_loop: fan-in "
            f"status={review['round_status']} "
            f"reviewed={reviewed_count}/{len(outcomes)} "
            f"failed={failed_count} "
            f"winner={review['selected_candidate_id'] or 'none'}"
        )
        print(
            "dual_agent_loop: portfolio review = "
            f"experiments/{current_plan.cycle_id}/planning/portfolio_review.json"
        )

        infrastructure_failures = tuple(
            item for item in outcomes if _coding_infrastructure_failed(item)
        )
        if infrastructure_failures:
            details = ", ".join(
                f"{item.branch_role}={item.build_status}"
                for item in infrastructure_failures
            )
            print(
                "dual_agent_loop: stopping — coding-agent infrastructure "
                "failure must be repaired before the next Planning round: "
                f"{details}"
            )
            break

        if not bool(review["quorum_reached"]):
            missing_roles = ", ".join(
                item.branch_role for item in outcomes if item.status != "reviewed"
            )
            print(
                "dual_agent_loop: stopping — both branch reviews are required "
                "before the next Planning round; missing valid review from: "
                f"{missing_roles or 'unknown'}"
            )
            break
        if iteration == args.max_cycles:
            print("dual_agent_loop: stopping — reached --max-cycles")
            break

        next_cycle = _increment_cycle_id(current_plan.cycle_id)
        current_plan = _load_or_create_next_plan(
            repo_root=repo_root,
            current_plan=current_plan,
            portfolio_review=review,
            next_cycle_id=next_cycle,
            timeout_seconds=args.timeout_seconds,
            build_timeout_seconds=args.build_timeout_seconds,
            planner_mode=args.planner_mode,
        )
        _print_plan(current_plan, repo_root)

    return 1 if had_failed_branch else 0


def _load_or_create_next_plan(
    *,
    repo_root: Path,
    current_plan: PortfolioPlan,
    portfolio_review: dict[str, object],
    next_cycle_id: str,
    timeout_seconds: float,
    build_timeout_seconds: float,
    planner_mode: str,
) -> PortfolioPlan:
    """Reuse a lineage-matching plan or regenerate a stale downstream plan."""

    path = portfolio_plan_path(repo_root, next_cycle_id)
    overwrite = False
    if path.is_file():
        try:
            return load_portfolio_plan(repo_root, next_cycle_id)
        except ValueError as exc:
            message = str(exc)
            if message not in (
                "portfolio parent plan lineage mismatch",
                "portfolio parent review lineage mismatch",
            ):
                raise
            overwrite = True
            print(
                "dual_agent_loop: regenerating stale downstream Planning "
                f"dispatch {next_cycle_id}: {message}"
            )

    created = PlanningAgent.create_next_parallel_coding_dispatch(
        repo_root=repo_root,
        current_plan=current_plan,
        portfolio_review=portfolio_review,
        next_cycle_id=next_cycle_id,
        timeout_seconds=timeout_seconds,
        build_timeout_seconds=build_timeout_seconds,
        planner_mode=planner_mode,
        overwrite=overwrite,
    )
    assert isinstance(created, PortfolioPlan)
    return created


def _acquire_campaign_lock(repo_root: Path):
    lock_root = repo_root / "experiments" / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    # Candidate and planning paths are shared across portfolio ids, so the
    # repository (not a caller-controlled portfolio label) is the lock scope.
    handle = (lock_root / "dual_agent_campaign.lock").open(
        "a+", encoding="utf-8"
    )
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
            except Exception as exc:  # all-settled boundary
                try:
                    _append_branch_failure_log(
                        repo_root=repo_root,
                        cycle_id=plan.cycle_id,
                        candidate_id=branch.candidate_id,
                        message=traceback.format_exc(),
                    )
                except (OSError, ValueError):
                    pass
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

    _branch_log_path(repo_root, plan.cycle_id, branch.candidate_id).unlink(
        missing_ok=True
    )
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
    except Exception as exc:  # converted into an all-settled lane result
        try:
            context = CycleContext.from_assignment_file(
                repo_root, branch.assignment_path
            )
            _append_branch_failure_log(
                repo_root=repo_root,
                cycle_id=context.cycle_id,
                candidate_id=context.candidate_id,
                message=traceback.format_exc(),
            )
        except Exception:
            pass
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
    try:
        assignment_value = command[command.index("--assignment") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("branch command is missing --assignment") from exc
    assignment = Path(assignment_value)
    assignment_path = safe_repo_path(
        cwd,
        assignment if assignment.is_absolute() else cwd / assignment,
    )
    context = CycleContext.from_assignment_file(cwd, assignment_path)
    log_path = _branch_log_path(cwd, context.cycle_id, context.candidate_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{context.candidate_id}] running: {' '.join(command)}", flush=True)
    environment = dict(os.environ)
    environment["PYTHONUNBUFFERED"] = "1"
    with log_path.open("w", encoding="utf-8") as log_stream:
        with subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        ) as process:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    log_stream.write(line)
                    log_stream.flush()
                    print(f"[{context.candidate_id}] {line}", end="", flush=True)
                return process.wait()
            except BaseException:
                _terminate_process_group(process)
                raise


def _branch_log_path(
    repo_root: Path,
    cycle_id: str,
    candidate_id: str,
) -> Path:
    cycle_id = validate_portfolio_cycle_id(cycle_id)
    candidate_id = validate_candidate_id(candidate_id)
    return safe_repo_path(
        repo_root,
        repo_root.resolve()
        / "experiments"
        / cycle_id
        / "planning"
        / "branch_logs"
        / f"{candidate_id}.log",
    )


def _append_branch_failure_log(
    *,
    repo_root: Path,
    cycle_id: str,
    candidate_id: str,
    message: str,
) -> None:
    path = _branch_log_path(repo_root, cycle_id, candidate_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n[coordinator_failure]\n")
        stream.write(message.rstrip() + "\n")


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Reap the branch process tree if log pumping or cancellation fails."""

    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def _print_branch_outcomes(
    *,
    repo_root: Path,
    cycle_id: str,
    outcomes: Sequence[BranchOutcome],
) -> None:
    for outcome in outcomes:
        return_code = "none" if outcome.return_code is None else str(outcome.return_code)
        print(
            "dual_agent_loop: branch "
            f"role={outcome.branch_role} "
            f"candidate={outcome.candidate_id} "
            f"status={outcome.status} "
            f"review_valid={str(outcome.status == 'reviewed').lower()} "
            f"return_code={return_code} "
            f"decision={outcome.decision} "
            f"eligible={str(outcome.eligible_for_promotion).lower()}"
        )
        if outcome.build_status:
            print(f"  build_status: {_terminal_field(outcome.build_status)}")
        if outcome.review_reason:
            print(f"  reason: {_terminal_field(outcome.review_reason)}")
        if outcome.next_action:
            print(f"  next_action: {_terminal_field(outcome.next_action)}")
        if outcome.error:
            if _is_expected_negative_exit(outcome):
                print("  settled_negative_exit: 1")
            else:
                label = "runner" if outcome.status == "reviewed" else "error"
                print(f"  {label}: {_terminal_field(outcome.error)}")
        print(f"  review: {outcome.review_path}")
        log_path = _branch_log_path(repo_root, cycle_id, outcome.candidate_id)
        if log_path.is_file():
            print(f"  log: {log_path.relative_to(repo_root.resolve())}")


def _is_expected_negative_exit(outcome: BranchOutcome) -> bool:
    return (
        outcome.status == "reviewed"
        and outcome.return_code == 1
        and outcome.decision != "ACCEPT_FOR_NEXT_CYCLE"
        and outcome.error == "pipeline return code 1"
        and not _coding_infrastructure_failed(outcome)
    )


def _coding_infrastructure_failed(outcome: BranchOutcome) -> bool:
    return is_coding_infrastructure_failure_status(outcome.build_status)


def _branch_execution_failed(outcome: BranchOutcome) -> bool:
    """Separate settled negative candidates from infrastructure failures."""

    if outcome.status != "reviewed":
        return True
    if _coding_infrastructure_failed(outcome):
        return True
    if _is_expected_negative_exit(outcome):
        return False
    if outcome.return_code not in (0,):
        return True
    return bool(outcome.error)


def _terminal_field(value: object, max_chars: int = 2000) -> str:
    text = " ".join(str(value).split())
    printable = "".join(
        character if character.isprintable() else "?" for character in text
    )
    if len(printable) <= max_chars:
        return printable
    return printable[: max_chars - 16].rstrip() + " ...[truncated]"


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
