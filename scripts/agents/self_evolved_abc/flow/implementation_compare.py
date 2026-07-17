"""CEC-first implementation comparison for coding-agent candidates."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.command_io import render_command_log
from scripts.agents.self_evolved_abc.flow.contracts import (
    ABC_RC_PATH,
    CANDIDATE_BUILD_READY_STATUSES,
    DEFAULT_ABC_BIN,
    FLOW_CANDIDATE_ABC_FLOW,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    IMPL_BASELINE_LABEL as BASELINE_LABEL,
    IMPL_CANDIDATE_LABEL as CANDIDATE_LABEL,
)
from scripts.agents.self_evolved_abc.flow.materialization import (
    candidate_flow_relative_path,
)
from scripts.agents.self_evolved_abc.flow.multi_flow import (
    EvaluationFlow,
    aggregate_flow_comparison_rows,
    flow_commands,
    normalize_evaluation_flows,
    normalize_flow_aggregation,
)
from scripts.agents.self_evolved_abc.flow.metrics import (
    parse_last_ps_metrics_text,
    parse_log_header_float,
    parse_log_header_int,
    strip_ansi,
)
from scripts.agents.self_evolved_abc.flow.paths import (
    ensure_dirs,
    impl_compare_root,
    repo_relative_path,
    repo_relative_existing_path,
)
from scripts.agents.self_evolved_abc.flow.verilog_frontend import (
    FrontendResult,
    benchmark_key,
    prepare_benchmark_frontend,
    write_frontend_summary_csv,
)
QOR_FIELDS = (
    "flow_id",
    "benchmark",
    "frontend_kind",
    "frontend_status",
    "frontend_input_path",
    "frontend_log_path",
    "implementation_label",
    "abc_exit_code",
    "aig_nodes",
    "aig_depth",
    "runtime_seconds",
    "aig_path",
    "log_path",
    "skipped_reason",
)
CEC_FIELDS = (
    "flow_id",
    "benchmark",
    "baseline_aig",
    "candidate_aig",
    "cec_exit_code",
    "cec_status",
    "runtime_seconds",
    "log_path",
    "skipped_reason",
)
QOR_DELTA_FIELDS = (
    "flow_id",
    "benchmark",
    "frontend_kind",
    "frontend_status",
    "cec_status",
    "correctness_backed",
    "baseline_aig_nodes",
    "candidate_aig_nodes",
    "and_delta_candidate_minus_baseline",
    "and_improve_pct",
    "baseline_aig_depth",
    "candidate_aig_depth",
    "depth_delta_candidate_minus_baseline",
    "baseline_runtime_seconds",
    "candidate_runtime_seconds",
    "runtime_delta_seconds",
    "flow_count",
    "cec_pass_flow_count",
    "candidate_vote_count",
    "baseline_vote_count",
    "tie_vote_count",
    "invalid_flow_count",
    "flow_vote_outcome",
    "all_flows_nonregressing",
    "safe_for_promotion",
    "skipped_reason",
)
FLOW_VOTE_FIELDS = (
    "benchmark",
    "flow_count",
    "vote_quorum",
    "candidate_vote_count",
    "baseline_vote_count",
    "tie_vote_count",
    "invalid_flow_count",
    "flow_vote_outcome",
    "all_flows_cec_pass",
    "all_flows_nonregressing",
    "safe_for_promotion",
)


@dataclass(frozen=True)
class ImplRunResult:
    benchmark: str
    flow_id: str
    implementation_label: str
    frontend: FrontendResult
    command: str
    log_path: Path
    aig_path: Path
    abc_exit_code: int | None
    aig_nodes: int | None
    aig_depth: int | None
    runtime_seconds: float | None
    skipped_reason: str


@dataclass(frozen=True)
class CecResult:
    benchmark: str
    flow_id: str
    baseline_aig: Path
    candidate_aig: Path
    command: str
    log_path: Path
    cec_exit_code: int | None
    cec_status: str
    runtime_seconds: float | None
    skipped_reason: str


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare baseline and candidate ABC implementations with CEC first."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument(
        "--candidate-flow",
        type=Path,
        default=None,
        help="Shared ABC flow script. Defaults to configs/flows/<cycle>_<candidate>.abc.",
    )
    parser.add_argument(
        "--baseline-abc-bin",
        default=None,
        help="Baseline ABC binary. Defaults to S4 manifest, then FlowTune-OpenFPGA/abc.",
    )
    parser.add_argument(
        "--candidate-abc-bin",
        default=None,
        help="Candidate ABC binary. Defaults to S4 manifest, then FlowTune-OpenFPGA/abc.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--cec-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--yosys-bin",
        default=os.environ.get("EDA_AGENT_YOSYS_BIN", "yosys"),
        help="Yosys executable used to normalize Verilog benchmarks to BLIF.",
    )
    parser.add_argument(
        "--frontend-timeout-seconds",
        type=float,
        default=None,
        help="Timeout per Verilog frontend run. Defaults to --timeout-seconds.",
    )
    parser.add_argument(
        "--from-existing-logs",
        action="store_true",
        help="Rebuild comparison CSVs from existing impl_compare logs and AIGs.",
    )
    parser.add_argument(
        "--allow-missing-build-gate",
        action="store_true",
        help="Allow comparison even if S4c build_info does not show a passing gate.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    context = CycleContext.from_assignment_file(args.repo_root.resolve(), args.assignment)
    candidate_flow = repo_relative_existing_path(
        context,
        args.candidate_flow or candidate_flow_relative_path(context),
    )
    output_root = impl_compare_root(context)
    ensure_compare_dirs(output_root)
    frontend_timeout = (
        args.frontend_timeout_seconds
        if args.frontend_timeout_seconds is not None
        else args.timeout_seconds
    )
    if frontend_timeout <= 0:
        raise ValueError("frontend timeout must be > 0")

    build_status = read_candidate_build_status(output_root)
    candidate_mode = str(context.assignment.get("source_patch_mode", "")).strip()
    if args.allow_missing_build_gate and candidate_mode != FLOW_CANDIDATE_ABC_FLOW:
        write_blocked_summary(context, output_root, build_status)
        print(
            "blocked: --allow-missing-build-gate is restricted to abc_flow "
            "candidates"
        )
        return 2
    if (
        not args.allow_missing_build_gate
        and build_status not in CANDIDATE_BUILD_READY_STATUSES
    ):
        write_blocked_summary(context, output_root, build_status)
        print(f"blocked: candidate build gate is {build_status or 'missing'}")
        return 2
    baseline_abc_bin = resolve_manifest_abc_bin(
        context=context,
        output_root=output_root,
        implementation_label=BASELINE_LABEL,
        explicit=args.baseline_abc_bin,
    )
    candidate_abc_bin = resolve_manifest_abc_bin(
        context=context,
        output_root=output_root,
        implementation_label=CANDIDATE_LABEL,
        explicit=args.candidate_abc_bin,
    )
    if (
        candidate_mode == FLOW_CANDIDATE_SOURCE_PATCH_DIFF
        and same_binary_identity(context, baseline_abc_bin, candidate_abc_bin)
    ):
        write_blocked_summary(
            context,
            output_root,
            "candidate_binary_not_distinct",
        )
        print(
            "blocked: source_patch_diff must compare a separately built "
            "candidate binary"
        )
        return 2

    flow_specs = normalize_evaluation_flows(
        context.assignment.get("evaluation_flows")
    )
    flow_aggregation = normalize_flow_aggregation(
        context.assignment.get("flow_aggregation")
    )
    frontend_results: list[FrontendResult] = []
    baseline_results: list[ImplRunResult] = []
    candidate_results: list[ImplRunResult] = []
    cec_results: list[CecResult] = []
    for benchmark_text in context.evaluation_benchmark_scope:
        benchmark = repo_relative_existing_path(context, Path(benchmark_text))
        frontend = prepare_benchmark_frontend(
            context=context,
            output_root=output_root,
            benchmark=benchmark,
            yosys_bin=args.yosys_bin,
            timeout_seconds=frontend_timeout,
            from_existing_logs=args.from_existing_logs,
        )
        frontend_results.append(frontend)
        for flow in flow_specs:
            baseline = collect_impl_result(
                context=context,
                output_root=output_root,
                benchmark=benchmark,
                frontend=frontend,
                flow=flow,
                candidate_flow=candidate_flow,
                implementation_label=BASELINE_LABEL,
                abc_bin=baseline_abc_bin,
                timeout_seconds=args.timeout_seconds,
                from_existing_logs=args.from_existing_logs,
            )
            candidate = collect_impl_result(
                context=context,
                output_root=output_root,
                benchmark=benchmark,
                frontend=frontend,
                flow=flow,
                candidate_flow=candidate_flow,
                implementation_label=CANDIDATE_LABEL,
                abc_bin=candidate_abc_bin,
                timeout_seconds=args.timeout_seconds,
                from_existing_logs=args.from_existing_logs,
            )
            baseline_results.append(baseline)
            candidate_results.append(candidate)
            cec_results.append(
                collect_cec_result(
                    context=context,
                    output_root=output_root,
                    baseline=baseline,
                    candidate=candidate,
                    abc_bin=baseline_abc_bin,
                    timeout_seconds=args.cec_timeout_seconds,
                    from_existing_logs=args.from_existing_logs,
                )
            )

    write_impl_summary_csv(
        context,
        output_root,
        "baseline_flow_summary.csv",
        baseline_results,
    )
    write_impl_summary_csv(
        context,
        output_root,
        "candidate_flow_summary.csv",
        candidate_results,
    )
    write_frontend_summary_csv(
        context=context,
        output_root=output_root,
        results=frontend_results,
    )
    write_cec_summary_csv(
        context,
        output_root,
        aggregate_cec_results(cec_results, flow_specs),
    )
    write_cec_summary_csv(
        context,
        output_root,
        cec_results,
        filename="cec_by_flow.csv",
    )
    detailed_delta_rows = build_qor_delta_rows(
        baseline_results, candidate_results, cec_results
    )
    write_qor_delta_csv(
        output_root, detailed_delta_rows, filename="qor_delta_by_flow.csv"
    )
    delta_rows, vote_rows, multi_flow_summary = aggregate_flow_comparison_rows(
        detailed_delta_rows,
        flow_ids=[flow.flow_id for flow in flow_specs],
        aggregation=flow_aggregation,
    )
    write_qor_delta_csv(output_root, delta_rows)
    write_flow_vote_csv(output_root, vote_rows)
    write_multi_flow_summary(output_root, multi_flow_summary)
    summary = write_impl_compare_summary(
        context=context,
        output_root=output_root,
        build_status=build_status or "unknown",
        baseline_results=baseline_results,
        candidate_results=candidate_results,
        cec_results=aggregate_cec_results(cec_results, flow_specs),
        delta_rows=delta_rows,
        flow_specs=flow_specs,
        multi_flow_summary=multi_flow_summary,
    )

    print(f"comparison_summary: {summary}")
    print(f"cec_rows: {len(delta_rows)}")
    print(f"cec_by_flow_rows: {len(cec_results)}")
    print(f"qor_delta_rows: {len(delta_rows)}")
    return 0 if all(
        result.cec_status == "cec_pass"
        for result in aggregate_cec_results(cec_results, flow_specs)
    ) else 1


def collect_impl_result(
    *,
    context: CycleContext,
    output_root: Path,
    benchmark: Path,
    frontend: FrontendResult,
    flow: EvaluationFlow,
    candidate_flow: Path,
    implementation_label: str,
    abc_bin: str,
    timeout_seconds: float,
    from_existing_logs: bool,
) -> ImplRunResult:
    design = benchmark_key(benchmark)
    side_root = output_root / implementation_label
    log_path = side_root / "logs" / flow.flow_id / f"{design}.qor.log"
    aig_path = side_root / "outputs" / flow.flow_id / f"{design}.aig"
    abc_script = render_qor_script(
        input_path=(
            frontend.input_path.relative_to(context.repo_root)
            if frontend.input_path is not None
            else Path("missing_frontend_input.blif")
        ),
        candidate_flow=candidate_flow,
        flow=flow,
        aig_path=aig_path.relative_to(context.repo_root),
    )
    command = render_shell_command(abc_bin, abc_script)
    if from_existing_logs:
        return load_impl_result_from_log(
            context=context,
            benchmark=benchmark,
            frontend=frontend,
            flow=flow,
            implementation_label=implementation_label,
            command=command,
            log_path=log_path,
            aig_path=aig_path,
        )
    return run_impl_command(
        context=context,
        benchmark=benchmark,
        frontend=frontend,
        flow=flow,
        implementation_label=implementation_label,
        command=command,
        abc_argv=(abc_bin, "-c", abc_script),
        log_path=log_path,
        aig_path=aig_path,
        timeout_seconds=timeout_seconds,
    )


def run_impl_command(
    *,
    context: CycleContext,
    benchmark: Path,
    frontend: FrontendResult,
    flow: EvaluationFlow,
    implementation_label: str,
    command: str,
    abc_argv: Sequence[str],
    log_path: Path,
    aig_path: Path,
    timeout_seconds: float,
) -> ImplRunResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    aig_path.parent.mkdir(parents=True, exist_ok=True)
    if not frontend.ready:
        log_path.write_text(
            render_command_log(
                command=command,
                return_code=None,
                runtime_seconds=0.0,
                output=(
                    "FRONTEND_SKIPPED: "
                    f"{frontend.frontend_status}: {frontend.skipped_reason}\n"
                ),
            ),
            encoding="utf-8",
        )
        return ImplRunResult(
            benchmark=str(benchmark),
            flow_id=flow.flow_id,
            implementation_label=implementation_label,
            frontend=frontend,
            command=command,
            log_path=log_path,
            aig_path=aig_path,
            abc_exit_code=None,
            aig_nodes=None,
            aig_depth=None,
            runtime_seconds=None,
            skipped_reason=f"frontend:{frontend.frontend_status}",
        )
    start = time.monotonic()
    try:
        completed = subprocess.run(
            tuple(abc_argv),
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        runtime_seconds = time.monotonic() - start
        log_path.write_text(
            render_command_log(
                command=command,
                return_code=None,
                runtime_seconds=runtime_seconds,
                output=f"EXEC_ERROR: {exc}\n",
            ),
            encoding="utf-8",
        )
        return ImplRunResult(
            benchmark=str(benchmark),
            flow_id=flow.flow_id,
            implementation_label=implementation_label,
            frontend=frontend,
            command=command,
            log_path=log_path,
            aig_path=aig_path,
            abc_exit_code=None,
            aig_nodes=None,
            aig_depth=None,
            runtime_seconds=runtime_seconds,
            skipped_reason=f"exec_error:{exc.__class__.__name__}",
        )
    except subprocess.TimeoutExpired as exc:
        runtime_seconds = time.monotonic() - start
        output = exc.stdout or ""
        log_path.write_text(
            render_command_log(
                command=command,
                return_code=None,
                runtime_seconds=runtime_seconds,
                output=f"{output}\nTIMEOUT after {timeout_seconds:g} seconds\n",
            ),
            encoding="utf-8",
        )
        return ImplRunResult(
            benchmark=str(benchmark),
            flow_id=flow.flow_id,
            implementation_label=implementation_label,
            frontend=frontend,
            command=command,
            log_path=log_path,
            aig_path=aig_path,
            abc_exit_code=None,
            aig_nodes=None,
            aig_depth=None,
            runtime_seconds=runtime_seconds,
            skipped_reason=f"timeout_after_{timeout_seconds:g}s",
        )

    runtime_seconds = time.monotonic() - start
    output = completed.stdout or ""
    log_path.write_text(
        render_command_log(
            command=command,
            return_code=completed.returncode,
            runtime_seconds=runtime_seconds,
            output=output,
        ),
        encoding="utf-8",
    )
    metrics = parse_last_ps_metrics_text(output)
    skipped_reason = ""
    if completed.returncode != 0:
        skipped_reason = f"abc_exit_code={completed.returncode}"
    elif metrics is None:
        skipped_reason = "missing_parseable_ps_metrics"
    elif not aig_path.exists():
        skipped_reason = "missing_expected_aig_output"
    return ImplRunResult(
        benchmark=str(benchmark),
        flow_id=flow.flow_id,
        implementation_label=implementation_label,
        frontend=frontend,
        command=command,
        log_path=log_path,
        aig_path=aig_path,
        abc_exit_code=completed.returncode,
        aig_nodes=metrics.ands if metrics else None,
        aig_depth=metrics.lev if metrics else None,
        runtime_seconds=runtime_seconds,
        skipped_reason=skipped_reason,
    )


def load_impl_result_from_log(
    *,
    context: CycleContext,
    benchmark: Path,
    frontend: FrontendResult,
    flow: EvaluationFlow,
    implementation_label: str,
    command: str,
    log_path: Path,
    aig_path: Path,
) -> ImplRunResult:
    if not log_path.exists():
        return ImplRunResult(
            benchmark=str(benchmark),
            flow_id=flow.flow_id,
            implementation_label=implementation_label,
            frontend=frontend,
            command=command,
            log_path=log_path,
            aig_path=aig_path,
            abc_exit_code=None,
            aig_nodes=None,
            aig_depth=None,
            runtime_seconds=None,
            skipped_reason="missing_log",
        )
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return_code = parse_log_header_int(text, "return_code")
    runtime_seconds = parse_log_header_float(text, "runtime_seconds")
    metrics = parse_last_ps_metrics_text(text)
    skipped_reason = ""
    if return_code != 0:
        skipped_reason = f"abc_exit_code={return_code}"
    elif metrics is None:
        skipped_reason = "missing_parseable_ps_metrics"
    elif not aig_path.exists():
        skipped_reason = "missing_expected_aig_output"
    return ImplRunResult(
        benchmark=str(benchmark),
        flow_id=flow.flow_id,
        implementation_label=implementation_label,
        frontend=frontend,
        command=command,
        log_path=log_path,
        aig_path=aig_path,
        abc_exit_code=return_code,
        aig_nodes=metrics.ands if metrics else None,
        aig_depth=metrics.lev if metrics else None,
        runtime_seconds=runtime_seconds,
        skipped_reason=skipped_reason,
    )


def collect_cec_result(
    *,
    context: CycleContext,
    output_root: Path,
    baseline: ImplRunResult,
    candidate: ImplRunResult,
    abc_bin: str,
    timeout_seconds: float,
    from_existing_logs: bool,
) -> CecResult:
    design = benchmark_key(Path(baseline.benchmark))
    log_path = (
        output_root
        / "comparison"
        / "logs"
        / baseline.flow_id
        / f"{design}.cec.log"
    )
    abc_script = f"cec {baseline.aig_path.relative_to(context.repo_root)} {candidate.aig_path.relative_to(context.repo_root)}"
    command = render_shell_command(abc_bin, abc_script)
    if baseline.skipped_reason or candidate.skipped_reason:
        skipped = "; ".join(
            item
            for item in (
                f"baseline:{baseline.skipped_reason}" if baseline.skipped_reason else "",
                f"candidate:{candidate.skipped_reason}" if candidate.skipped_reason else "",
            )
            if item
        )
        write_cec_skip_log(log_path, command=command, skipped_reason=skipped)
        return CecResult(
            benchmark=baseline.benchmark,
            flow_id=baseline.flow_id,
            baseline_aig=baseline.aig_path,
            candidate_aig=candidate.aig_path,
            command=command,
            log_path=log_path,
            cec_exit_code=None,
            cec_status="cec_skipped",
            runtime_seconds=None,
            skipped_reason=skipped,
        )
    if not baseline.aig_path.exists() or not candidate.aig_path.exists():
        write_cec_skip_log(
            log_path,
            command=command,
            skipped_reason="missing_aig_for_cec",
        )
        return CecResult(
            benchmark=baseline.benchmark,
            flow_id=baseline.flow_id,
            baseline_aig=baseline.aig_path,
            candidate_aig=candidate.aig_path,
            command=command,
            log_path=log_path,
            cec_exit_code=None,
            cec_status="cec_skipped",
            runtime_seconds=None,
            skipped_reason="missing_aig_for_cec",
        )
    if from_existing_logs:
        return load_cec_result_from_log(
            benchmark=baseline.benchmark,
            flow_id=baseline.flow_id,
            baseline_aig=baseline.aig_path,
            candidate_aig=candidate.aig_path,
            command=command,
            log_path=log_path,
        )
    return run_cec_command(
        context=context,
        benchmark=baseline.benchmark,
        flow_id=baseline.flow_id,
        baseline_aig=baseline.aig_path,
        candidate_aig=candidate.aig_path,
        command=command,
        abc_argv=(abc_bin, "-c", abc_script),
        log_path=log_path,
        timeout_seconds=timeout_seconds,
    )


def run_cec_command(
    *,
    context: CycleContext,
    benchmark: str,
    flow_id: str,
    baseline_aig: Path,
    candidate_aig: Path,
    command: str,
    abc_argv: Sequence[str],
    log_path: Path,
    timeout_seconds: float,
) -> CecResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            tuple(abc_argv),
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        runtime_seconds = time.monotonic() - start
        log_path.write_text(
            render_command_log(
                command=command,
                return_code=None,
                runtime_seconds=runtime_seconds,
                output=f"EXEC_ERROR: {exc}\n",
            ),
            encoding="utf-8",
        )
        return CecResult(
            benchmark=benchmark,
            flow_id=flow_id,
            baseline_aig=baseline_aig,
            candidate_aig=candidate_aig,
            command=command,
            log_path=log_path,
            cec_exit_code=None,
            cec_status="cec_crash",
            runtime_seconds=runtime_seconds,
            skipped_reason=f"exec_error:{exc.__class__.__name__}",
        )
    except subprocess.TimeoutExpired as exc:
        runtime_seconds = time.monotonic() - start
        output = exc.stdout or ""
        log_path.write_text(
            render_command_log(
                command=command,
                return_code=None,
                runtime_seconds=runtime_seconds,
                output=f"{output}\nTIMEOUT after {timeout_seconds:g} seconds\n",
            ),
            encoding="utf-8",
        )
        return CecResult(
            benchmark=benchmark,
            flow_id=flow_id,
            baseline_aig=baseline_aig,
            candidate_aig=candidate_aig,
            command=command,
            log_path=log_path,
            cec_exit_code=None,
            cec_status="cec_timeout",
            runtime_seconds=runtime_seconds,
            skipped_reason=f"timeout_after_{timeout_seconds:g}s",
        )

    runtime_seconds = time.monotonic() - start
    output = completed.stdout or ""
    log_path.write_text(
        render_command_log(
            command=command,
            return_code=completed.returncode,
            runtime_seconds=runtime_seconds,
            output=output,
        ),
        encoding="utf-8",
    )
    status = parse_cec_status(output, completed.returncode)
    return CecResult(
        benchmark=benchmark,
        flow_id=flow_id,
        baseline_aig=baseline_aig,
        candidate_aig=candidate_aig,
        command=command,
        log_path=log_path,
        cec_exit_code=completed.returncode,
        cec_status=status,
        runtime_seconds=runtime_seconds,
        skipped_reason="" if status == "cec_pass" else status,
    )


def load_cec_result_from_log(
    *,
    benchmark: str,
    flow_id: str,
    baseline_aig: Path,
    candidate_aig: Path,
    command: str,
    log_path: Path,
) -> CecResult:
    if not log_path.exists():
        return CecResult(
            benchmark=benchmark,
            flow_id=flow_id,
            baseline_aig=baseline_aig,
            candidate_aig=candidate_aig,
            command=command,
            log_path=log_path,
            cec_exit_code=None,
            cec_status="cec_skipped",
            runtime_seconds=None,
            skipped_reason="missing_cec_log",
        )
    text = log_path.read_text(encoding="utf-8", errors="replace")
    exit_code = parse_log_header_int(text, "return_code")
    runtime_seconds = parse_log_header_float(text, "runtime_seconds")
    status = parse_cec_status(text, exit_code)
    return CecResult(
        benchmark=benchmark,
        flow_id=flow_id,
        baseline_aig=baseline_aig,
        candidate_aig=candidate_aig,
        command=command,
        log_path=log_path,
        cec_exit_code=exit_code,
        cec_status=status,
        runtime_seconds=runtime_seconds,
        skipped_reason="" if status == "cec_pass" else status,
    )


def parse_cec_status(output: str, return_code: int | None) -> str:
    text = strip_ansi(output).lower()
    if "not equivalent" in text or "not equal" in text:
        return "cec_fail"
    if "counter-example" in text or "counterexample" in text:
        return "cec_fail"
    if return_code not in (0, None):
        return "cec_crash"
    if "networks are equivalent" in text or "equivalent" in text:
        return "cec_pass"
    if "unsat" in text and "sat" not in text.replace("unsat", ""):
        return "cec_pass"
    return "cec_unparseable"


def build_qor_delta_rows(
    baseline_results: Sequence[ImplRunResult],
    candidate_results: Sequence[ImplRunResult],
    cec_results: Sequence[CecResult],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for baseline, candidate, cec in zip(baseline_results, candidate_results, cec_results):
        correctness_backed = cec.cec_status == "cec_pass"
        skipped = "; ".join(
            item
            for item in (baseline.skipped_reason, candidate.skipped_reason, cec.skipped_reason)
            if item
        )
        rows.append(
            {
                "benchmark": baseline.benchmark,
                "flow_id": baseline.flow_id,
                "frontend_kind": baseline.frontend.frontend_kind,
                "frontend_status": baseline.frontend.frontend_status,
                "cec_status": cec.cec_status,
                "correctness_backed": correctness_backed,
                "baseline_aig_nodes": empty_if_none(baseline.aig_nodes),
                "candidate_aig_nodes": empty_if_none(candidate.aig_nodes),
                "and_delta_candidate_minus_baseline": (
                    empty_if_none(subtract(candidate.aig_nodes, baseline.aig_nodes))
                    if correctness_backed
                    else ""
                ),
                "and_improve_pct": (
                    format_float(percent_improve(baseline.aig_nodes, candidate.aig_nodes))
                    if correctness_backed
                    else ""
                ),
                "baseline_aig_depth": empty_if_none(baseline.aig_depth),
                "candidate_aig_depth": empty_if_none(candidate.aig_depth),
                "depth_delta_candidate_minus_baseline": (
                    empty_if_none(subtract(candidate.aig_depth, baseline.aig_depth))
                    if correctness_backed
                    else ""
                ),
                "baseline_runtime_seconds": format_float(baseline.runtime_seconds),
                "candidate_runtime_seconds": format_float(candidate.runtime_seconds),
                "runtime_delta_seconds": (
                    format_float(subtract_float(candidate.runtime_seconds, baseline.runtime_seconds))
                    if correctness_backed
                    else ""
                ),
                "skipped_reason": skipped,
            }
        )
    return rows


def write_impl_summary_csv(
    context: CycleContext,
    output_root: Path,
    filename: str,
    results: Sequence[ImplRunResult],
) -> Path:
    path = output_root / "comparison" / filename
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=QOR_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "benchmark": result.benchmark,
                    "flow_id": result.flow_id,
                    "frontend_kind": result.frontend.frontend_kind,
                    "frontend_status": result.frontend.frontend_status,
                    "frontend_input_path": (
                        display_repo_path(context, result.frontend.input_path)
                        if result.frontend.input_path is not None
                        else ""
                    ),
                    "frontend_log_path": (
                        display_repo_path(context, result.frontend.log_path)
                        if result.frontend.log_path is not None
                        else ""
                    ),
                    "implementation_label": result.implementation_label,
                    "abc_exit_code": empty_if_none(result.abc_exit_code),
                    "aig_nodes": empty_if_none(result.aig_nodes),
                    "aig_depth": empty_if_none(result.aig_depth),
                    "runtime_seconds": format_float(result.runtime_seconds),
                    "aig_path": display_repo_path(context, result.aig_path),
                    "log_path": display_repo_path(context, result.log_path),
                    "skipped_reason": result.skipped_reason,
                }
            )
    return path


def write_cec_summary_csv(
    context: CycleContext,
    output_root: Path,
    results: Sequence[CecResult],
    filename: str = "cec_summary.csv",
) -> Path:
    path = output_root / "comparison" / filename
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CEC_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "benchmark": result.benchmark,
                    "flow_id": result.flow_id,
                    "baseline_aig": display_repo_path(context, result.baseline_aig),
                    "candidate_aig": display_repo_path(context, result.candidate_aig),
                    "cec_exit_code": empty_if_none(result.cec_exit_code),
                    "cec_status": result.cec_status,
                    "runtime_seconds": format_float(result.runtime_seconds),
                    "log_path": display_repo_path(context, result.log_path),
                    "skipped_reason": result.skipped_reason,
                }
            )
    return path


def display_repo_path(context: CycleContext, path: Path) -> str:
    return str(repo_relative_path(context, path))


def write_qor_delta_csv(
    output_root: Path,
    rows: Sequence[dict[str, object]],
    *,
    filename: str = "qor_delta.csv",
) -> Path:
    path = output_root / "comparison" / filename
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=QOR_DELTA_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_flow_vote_csv(
    output_root: Path,
    rows: Sequence[dict[str, object]],
) -> Path:
    path = output_root / "comparison" / "flow_vote_summary.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FLOW_VOTE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_multi_flow_summary(
    output_root: Path,
    summary: Mapping[str, object],
) -> Path:
    path = output_root / "comparison" / "multi_flow_summary.json"
    path.write_text(
        json.dumps(dict(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def aggregate_cec_results(
    results: Sequence[CecResult],
    flow_specs: Sequence[EvaluationFlow],
) -> list[CecResult]:
    """Collapse per-flow CEC evidence into the legacy one-row-per-design gate."""

    expected = tuple(flow.flow_id for flow in flow_specs)
    grouped: dict[str, list[CecResult]] = {}
    for result in results:
        grouped.setdefault(result.benchmark, []).append(result)
    aggregate: list[CecResult] = []
    for benchmark in sorted(grouped):
        group = grouped[benchmark]
        by_flow = {result.flow_id: result for result in group}
        ordered = [by_flow[flow_id] for flow_id in expected if flow_id in by_flow]
        first = ordered[0] if ordered else group[0]
        statuses = [result.cec_status for result in ordered]
        all_present = len(ordered) == len(expected) and len(group) == len(expected)
        if all_present and statuses and all(status == "cec_pass" for status in statuses):
            status = "cec_pass"
            skipped_reason = ""
        else:
            status = _aggregate_cec_status(statuses)
            missing = [flow_id for flow_id in expected if flow_id not in by_flow]
            reasons = [result.skipped_reason for result in ordered if result.skipped_reason]
            if missing:
                reasons.append(f"missing_flow={','.join(missing)}")
            if len(group) != len(by_flow):
                reasons.append("duplicate_flow_rows")
            skipped_reason = "; ".join(sorted(set(reasons))) or status
        exit_codes = [
            result.cec_exit_code
            for result in ordered
            if result.cec_exit_code is not None
        ]
        runtimes = [
            result.runtime_seconds
            for result in ordered
            if result.runtime_seconds is not None
        ]
        aggregate.append(
            CecResult(
                benchmark=benchmark,
                flow_id="aggregate_all",
                baseline_aig=first.baseline_aig,
                candidate_aig=first.candidate_aig,
                command="multi-flow CEC aggregate",
                log_path=first.log_path,
                cec_exit_code=(0 if status == "cec_pass" else (exit_codes[-1] if exit_codes else None)),
                cec_status=status,
                runtime_seconds=sum(runtimes) if runtimes else None,
                skipped_reason=skipped_reason,
            )
        )
    return aggregate


def _aggregate_cec_status(statuses: Sequence[str]) -> str:
    for status in ("cec_fail", "cec_timeout", "cec_crash", "cec_skipped"):
        if status in statuses:
            return status
    return "cec_unparseable"


def write_cec_skip_log(path: Path, *, command: str, skipped_reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_command_log(
            command=command,
            return_code=None,
            runtime_seconds=0.0,
            output=f"CEC_SKIPPED: {skipped_reason}\n",
        ),
        encoding="utf-8",
    )


def write_impl_compare_summary(
    *,
    context: CycleContext,
    output_root: Path,
    build_status: str,
    baseline_results: Sequence[ImplRunResult],
    candidate_results: Sequence[ImplRunResult],
    cec_results: Sequence[CecResult],
    delta_rows: Sequence[dict[str, object]],
    flow_specs: Sequence[EvaluationFlow],
    multi_flow_summary: Mapping[str, object],
) -> Path:
    path = output_root / "comparison" / "impl_compare_summary.md"
    cec_pass = sum(1 for result in cec_results if result.cec_status == "cec_pass")
    complete_rows = sum(
        1
        for baseline, candidate in zip(baseline_results, candidate_results)
        if not baseline.skipped_reason and not candidate.skipped_reason
    )
    backed_rows = [row for row in delta_rows if row["correctness_backed"]]
    avg_and_improve = average_float(row["and_improve_pct"] for row in backed_rows)
    comparison_reviewable = (
        build_status in CANDIDATE_BUILD_READY_STATUSES
        and cec_pass == len(cec_results)
        and len(cec_results) > 0
    )
    lines = [
        f"# Implementation Compare Summary -- {context.cycle_id} {context.candidate_id}",
        "",
        "## Decision Gate",
        "",
        f"- Candidate build status: `{build_status}`",
        f"- QoR rows complete: {complete_rows}/{len(baseline_results)} per-flow runs",
        f"- Frozen flows: {', '.join(flow.flow_id for flow in flow_specs)}",
        f"- CEC pass: {cec_pass}/{len(cec_results)}",
        f"- Correctness-backed delta rows: {len(backed_rows)}/{len(delta_rows)}",
        f"- Average AND improvement pct: `{format_float(avg_and_improve)}`",
        f"- Candidate flow-vote wins: {multi_flow_summary.get('candidate_vote_wins', 0)}/{len(delta_rows)}",
        f"- Safe multi-flow rows: {multi_flow_summary.get('safe_for_promotion_count', 0)}/{len(delta_rows)}",
        f"- Comparison reviewable: `{str(comparison_reviewable).lower()}`",
        "- Champion promotion: decided only by `review_decision.json` thresholds",
        "",
        "## Artifacts",
        "",
        "- `baseline_flow_summary.csv`",
        "- `candidate_flow_summary.csv`",
        "- `frontend_summary.csv`",
        "- `cec_summary.csv`",
        "- `cec_by_flow.csv`",
        "- `qor_delta.csv`",
        "- `qor_delta_by_flow.csv`",
        "- `flow_vote_summary.csv` and `multi_flow_summary.json`",
        f"- logs under `../{BASELINE_LABEL}/logs/`, `../{CANDIDATE_LABEL}/logs/`, and `logs/`",
        f"- AIG outputs under `../{BASELINE_LABEL}/outputs/` and `../{CANDIDATE_LABEL}/outputs/`",
        "",
        "## Policy",
        "",
        "- QoR deltas are reviewable only when `correctness_backed` is true.",
        "- Any CEC fail, timeout, crash, skip, frontend failure, or unparseable result blocks promotion.",
        "- Verilog benchmarks are normalized once by Yosys to BLIF, then reused by every flow and both implementations.",
        "- Median QoR aggregation is paired with strict-majority voting; promotion additionally requires no per-flow AND regression by default.",
        "- This runner does not update the active rulebase.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_blocked_summary(
    context: CycleContext,
    output_root: Path,
    build_status: str | None,
) -> Path:
    path = output_root / "comparison" / "impl_compare_summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                f"# Implementation Compare Summary -- {context.cycle_id} {context.candidate_id}",
                "",
                "## Decision Gate",
                "",
                f"- Candidate build status: `{build_status or 'missing'}`",
                "- Promotion allowed: `false`",
                "",
                "S5/F7 did not run because S4 has not produced a build-ready "
                "candidate status.",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def render_qor_script(
    *,
    input_path: Path | None = None,
    benchmark: Path | None = None,
    candidate_flow: Path,
    flow: EvaluationFlow | None = None,
    aig_path: Path,
) -> str:
    """Render one ABC QoR command, preserving the legacy keyword shape.

    ``benchmark=`` was the original direct-ABC API. New callers pass
    ``input_path=`` after the frontend has prepared a normalized BLIF. Keeping
    both forms makes manual BLIF/bench invocations continue to work.
    """

    resolved_input = input_path or benchmark
    if resolved_input is None:
        raise ValueError("render_qor_script requires input_path or benchmark")
    resolved_flow = flow or EvaluationFlow(
        flow_id="candidate_recipe",
        kind="candidate_recipe",
        commands=(),
    )
    return "; ".join(
        (
            f"source {ABC_RC_PATH}",
            f"read {resolved_input}",
            *flow_commands(
                resolved_flow,
                candidate_flow_path=candidate_flow.as_posix(),
            ),
            "strash",
            f"write_aiger {aig_path}",
            "ps",
        )
    )


def render_shell_command(abc_bin: str, abc_script: str) -> str:
    return shlex.join((abc_bin, "-c", abc_script))


def resolve_manifest_abc_bin(
    *,
    context: CycleContext,
    output_root: Path,
    implementation_label: str,
    explicit: str | None,
) -> str:
    if explicit:
        return explicit
    path = output_root / implementation_label / "build_info.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        binary_path = str(payload.get("binary_path", "")).strip()
        if binary_path:
            return binary_path
    return str(DEFAULT_ABC_BIN)


def read_candidate_build_status(output_root: Path) -> str | None:
    path = output_root / CANDIDATE_LABEL / "build_info.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return str(payload.get("status", "")).strip() or None


def same_binary_identity(
    context: CycleContext,
    baseline_abc_bin: str,
    candidate_abc_bin: str,
) -> bool:
    """Return whether both comparison sides resolve to the same executable."""

    if baseline_abc_bin == candidate_abc_bin:
        return True

    def resolved(value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = context.repo_root / path
        return path.resolve()

    return resolved(baseline_abc_bin) == resolved(candidate_abc_bin)


def ensure_compare_dirs(output_root: Path) -> None:
    ensure_dirs(
        output_root / BASELINE_LABEL / "logs",
        output_root / BASELINE_LABEL / "outputs",
        output_root / CANDIDATE_LABEL / "logs",
        output_root / CANDIDATE_LABEL / "outputs",
        output_root / "comparison" / "logs",
    )


def subtract(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left - right


def subtract_float(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def percent_improve(baseline: int | None, candidate: int | None) -> float | None:
    if baseline in (None, 0) or candidate is None:
        return None
    return 100.0 * (baseline - candidate) / baseline


def average_float(values: Sequence[object]) -> float | None:
    parsed: list[float] = []
    for value in values:
        if value in ("", None):
            continue
        parsed.append(float(value))
    if not parsed:
        return None
    return sum(parsed) / len(parsed)


def empty_if_none(value: object) -> object:
    return "" if value is None else value


def format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


if __name__ == "__main__":
    raise SystemExit(main())
