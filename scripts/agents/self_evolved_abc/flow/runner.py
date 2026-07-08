"""Minimal runner for Flow Agent evaluation."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.flow.command_io import render_command_log
from scripts.agents.self_evolved_abc.flow.metrics import (
    parse_last_ps_metrics_text,
    parse_log_header_float,
    parse_log_header_int,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.evaluation import (
    FlowEvaluationCase,
    build_flow_evaluation_cases,
    render_abc_argv,
    render_abc_qor_command,
    render_abc_shell_command,
    render_baseline_qor_command,
    result_csv_header,
)
from scripts.agents.self_evolved_abc.flow.materialization import (
    candidate_flow_relative_path,
)
from scripts.agents.self_evolved_abc.flow.paths import (
    ensure_dirs,
    logs_dir,
    outputs_dir,
    results_dir,
)


@dataclass(frozen=True)
class FlowRunResult:
    benchmark: str
    flow_label: str
    command: str
    log_path: Path
    output_aig_path: Path
    abc_exit_code: int | None
    aig_nodes: int | None
    aig_depth: int | None
    runtime_seconds: float | None
    skipped_reason: str
    correctness_status: str


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run flow evaluation commands and write artifacts."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--assignment",
        type=Path,
        required=True,
        help="Cycle assignment JSON.",
    )
    parser.add_argument(
        "--candidate-flow",
        type=Path,
        default=None,
        help="Candidate ABC flow. Defaults to configs/flows/<cycle>_<candidate>.abc.",
    )
    parser.add_argument(
        "--abc-bin",
        default="abc",
        help="ABC executable on the current machine or remote host.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Timeout per ABC command.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands and write not_run rows without executing ABC.",
    )
    parser.add_argument(
        "--from-existing-logs",
        action="store_true",
        help="Rebuild result CSV and run notes from existing logs and AIG outputs.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    context = CycleContext.from_assignment_file(repo_root, args.assignment)

    candidate_flow = args.candidate_flow or candidate_flow_relative_path(context)
    cases = build_flow_evaluation_cases(
        context=context,
        candidate_flow=candidate_flow,
    )

    ensure_cycle_dirs(context)

    results: list[FlowRunResult] = []
    for case in cases:
        baseline = render_baseline_qor_command(case)
        candidate = render_abc_qor_command(case)
        baseline_command = render_abc_shell_command(args.abc_bin, baseline.abc_script)
        candidate_command = render_abc_shell_command(args.abc_bin, candidate.abc_script)
        if args.from_existing_logs:
            results.append(
                load_existing_flow_result(
                    context=context,
                    case=case,
                    flow_label=case.baseline_label,
                    command=baseline_command,
                )
            )
            results.append(
                load_existing_flow_result(
                    context=context,
                    case=case,
                    flow_label=case.candidate_label,
                    command=candidate_command,
                )
            )
        else:
            results.append(
                run_one_flow(
                    context=context,
                    case=case,
                    flow_label=case.baseline_label,
                    abc_argv=render_abc_argv(args.abc_bin, baseline.abc_script),
                    command=baseline_command,
                    timeout_seconds=args.timeout_seconds,
                    dry_run=args.dry_run,
                )
            )
            results.append(
                run_one_flow(
                    context=context,
                    case=case,
                    flow_label=case.candidate_label,
                    abc_argv=render_abc_argv(args.abc_bin, candidate.abc_script),
                    command=candidate_command,
                    timeout_seconds=args.timeout_seconds,
                    dry_run=args.dry_run,
                )
            )

    write_flow_summary(context, results)
    write_run_notes(
        context,
        results,
        dry_run=args.dry_run,
        from_existing_logs=args.from_existing_logs,
    )

    print(f"wrote: {results_dir(context) / 'flow_summary.csv'}")
    print(f"wrote: {results_dir(context) / 'run_notes.md'}")
    print(f"rows: {len(results)}")
    return 0


def ensure_cycle_dirs(context: CycleContext) -> None:
    ensure_dirs(logs_dir(context), outputs_dir(context), results_dir(context))


def run_one_flow(
    *,
    context: CycleContext,
    case: FlowEvaluationCase,
    flow_label: str,
    abc_argv: Sequence[str],
    command: str,
    timeout_seconds: float,
    dry_run: bool,
) -> FlowRunResult:
    design = case.benchmark.stem
    log_path = logs_dir(context) / f"{design}.{flow_label}.log"
    output_aig_path = outputs_dir(context) / f"{design}.{flow_label}.aig"

    if dry_run:
        log_path.write_text(
            f"DRY RUN\ncommand: {command}\n",
            encoding="utf-8",
        )
        return FlowRunResult(
            benchmark=str(case.benchmark),
            flow_label=flow_label,
            command=command,
            log_path=log_path,
            output_aig_path=output_aig_path,
            abc_exit_code=None,
            aig_nodes=None,
            aig_depth=None,
            runtime_seconds=None,
            skipped_reason="dry_run",
            correctness_status="not_run",
        )

    start = time.monotonic()
    try:
        completed = subprocess.run(
            list(abc_argv),
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        runtime_seconds = time.monotonic() - start
        output_text = completed.stdout or ""
        log_path.write_text(
            render_command_log(
                command=command,
                return_code=completed.returncode,
                runtime_seconds=runtime_seconds,
                output=output_text,
            ),
            encoding="utf-8",
        )

        metrics = parse_last_ps_metrics_text(output_text)
        aig_nodes = metrics.ands if metrics is not None else None
        aig_depth = metrics.lev if metrics is not None else None
        skipped_reason = ""
        if completed.returncode != 0:
            skipped_reason = f"abc_exit_code={completed.returncode}"
        elif aig_nodes is None or aig_depth is None:
            skipped_reason = "missing_parseable_ps_metrics"
        elif not output_aig_path.exists():
            skipped_reason = "missing_expected_aig_output"

        return FlowRunResult(
            benchmark=str(case.benchmark),
            flow_label=flow_label,
            command=command,
            log_path=log_path,
            output_aig_path=output_aig_path,
            abc_exit_code=completed.returncode,
            aig_nodes=aig_nodes,
            aig_depth=aig_depth,
            runtime_seconds=runtime_seconds,
            skipped_reason=skipped_reason,
            correctness_status="provisional_no_cec",
        )

    except subprocess.TimeoutExpired as exc:
        runtime_seconds = time.monotonic() - start
        output_text = exc.stdout or ""
        log_path.write_text(
            render_command_log(
                command=command,
                return_code=None,
                runtime_seconds=runtime_seconds,
                output=f"{output_text}\nTIMEOUT after {timeout_seconds} seconds\n",
            ),
            encoding="utf-8",
        )
        return FlowRunResult(
            benchmark=str(case.benchmark),
            flow_label=flow_label,
            command=command,
            log_path=log_path,
            output_aig_path=output_aig_path,
            abc_exit_code=None,
            aig_nodes=None,
            aig_depth=None,
            runtime_seconds=runtime_seconds,
            skipped_reason=f"timeout_after_{timeout_seconds:g}s",
            correctness_status="not_run",
        )


def load_existing_flow_result(
    *,
    context: CycleContext,
    case: FlowEvaluationCase,
    flow_label: str,
    command: str,
) -> FlowRunResult:
    design = case.benchmark.stem
    log_path = logs_dir(context) / f"{design}.{flow_label}.log"
    output_aig_path = outputs_dir(context) / f"{design}.{flow_label}.aig"

    if not log_path.exists():
        return FlowRunResult(
            benchmark=str(case.benchmark),
            flow_label=flow_label,
            command=command,
            log_path=log_path,
            output_aig_path=output_aig_path,
            abc_exit_code=None,
            aig_nodes=None,
            aig_depth=None,
            runtime_seconds=None,
            skipped_reason="missing_log",
            correctness_status="not_run",
        )

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    return_code = parse_log_header_int(log_text, "return_code")
    runtime_seconds = parse_log_header_float(log_text, "runtime_seconds")
    metrics = parse_last_ps_metrics_text(log_text)
    aig_nodes = metrics.ands if metrics is not None else None
    aig_depth = metrics.lev if metrics is not None else None

    skipped_reason = ""
    if return_code != 0:
        skipped_reason = f"abc_exit_code={return_code}"
    elif aig_nodes is None or aig_depth is None:
        skipped_reason = "missing_parseable_ps_metrics"
    elif not output_aig_path.exists():
        skipped_reason = "missing_expected_aig_output"

    return FlowRunResult(
        benchmark=str(case.benchmark),
        flow_label=flow_label,
        command=command,
        log_path=log_path,
        output_aig_path=output_aig_path,
        abc_exit_code=return_code,
        aig_nodes=aig_nodes,
        aig_depth=aig_depth,
        runtime_seconds=runtime_seconds,
        skipped_reason=skipped_reason,
        correctness_status="provisional_no_cec" if return_code == 0 else "not_run",
    )


def write_flow_summary(
    context: CycleContext,
    results: Sequence[FlowRunResult],
) -> Path:
    path = results_dir(context) / "flow_summary.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result_csv_header()))
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "benchmark": result.benchmark,
                    "flow_label": result.flow_label,
                    "abc_exit_code": empty_if_none(result.abc_exit_code),
                    "aig_nodes": empty_if_none(result.aig_nodes),
                    "aig_depth": empty_if_none(result.aig_depth),
                    "runtime_seconds": format_float(result.runtime_seconds),
                    "skipped_reason": result.skipped_reason,
                    "correctness_status": result.correctness_status,
                }
            )
    return path


def write_run_notes(
    context: CycleContext,
    results: Sequence[FlowRunResult],
    *,
    dry_run: bool,
    from_existing_logs: bool = False,
) -> Path:
    path = results_dir(context) / "run_notes.md"
    complete = [
        result
        for result in results
        if not result.skipped_reason and result.abc_exit_code == 0
    ]
    skipped = [result for result in results if result.skipped_reason]

    lines = [
        f"# {context.cycle_id} F6 Flow Run Notes",
        "",
        "## Scope",
        "",
        f"- Candidate: {context.candidate_id}",
        f"- Agent: {context.agent_name}",
        f"- Rows written: {len(results)}",
        f"- Complete rows: {len(complete)}",
        f"- Skipped/problem rows: {len(skipped)}",
        f"- Dry run: {str(dry_run).lower()}",
        f"- From existing logs: {str(from_existing_logs).lower()}",
        "",
        "## Correctness Caveat",
        "",
        "- CEC is not automated in F6.",
        "- QoR rows with `provisional_no_cec` are process evidence only.",
        "- Do not promote this candidate as a champion until CEC passes.",
        "",
        "## Problem Rows",
        "",
    ]

    if skipped:
        for result in skipped:
            lines.append(
                f"- {result.benchmark} `{result.flow_label}`: {result.skipped_reason}"
            )
    else:
        lines.append("- None.")

    lines.extend(
        (
            "",
            "## Generated Artifacts",
            "",
            "- `flow_summary.csv`",
            "- `run_notes.md`",
            "- logs under `../logs/`",
            "- AIG outputs under `../outputs/` when ABC completed successfully",
            "",
        )
    )

    path.write_text("\n".join(lines), encoding="utf-8")
    return path

def empty_if_none(value: object) -> object:
    return "" if value is None else value


def format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


if __name__ == "__main__":
    raise SystemExit(main())
