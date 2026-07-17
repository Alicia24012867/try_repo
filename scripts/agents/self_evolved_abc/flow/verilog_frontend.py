"""Auditable Verilog-to-ABC input preparation.

The ABC comparison runner uses one normalized network for both baseline and
candidate executions.  This prevents frontend differences from being confused
with a source-patch QoR change and makes the forty Verilog benchmarks subject to
the same CEC gate as the existing BLIF suites.
"""

from __future__ import annotations

import csv
import hashlib
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.benchmarks import benchmark_frontend_kind
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.command_io import render_command_log
from scripts.agents.self_evolved_abc.flow.metrics import (
    parse_log_header_float,
    parse_log_header_int,
)
from scripts.agents.self_evolved_abc.flow.paths import repo_relative_path


FRONTEND_CSV_FIELDS = (
    "benchmark",
    "frontend_kind",
    "frontend_status",
    "input_path",
    "frontend_exit_code",
    "runtime_seconds",
    "log_path",
    "skipped_reason",
)


@dataclass(frozen=True)
class FrontendResult:
    """One source benchmark and the ABC-readable network prepared from it."""

    benchmark: Path
    input_path: Path | None
    frontend_kind: str
    frontend_status: str
    command: str
    log_path: Path | None
    frontend_exit_code: int | None
    runtime_seconds: float | None
    skipped_reason: str

    @property
    def ready(self) -> bool:
        return self.input_path is not None and self.frontend_status in (
            "abc_native",
            "yosys_pass",
        )


def prepare_benchmark_frontend(
    *,
    context: CycleContext,
    output_root: Path,
    benchmark: Path,
    yosys_bin: str,
    timeout_seconds: float,
    from_existing_logs: bool,
) -> FrontendResult:
    """Return an ABC-readable input, running Yosys only for Verilog sources."""

    source = _absolute_repo_path(context, benchmark)
    kind = benchmark_frontend_kind(benchmark)
    if kind == "abc_native":
        return FrontendResult(
            benchmark=benchmark,
            input_path=source,
            frontend_kind=kind,
            frontend_status="abc_native" if source.is_file() else "missing_input",
            command="",
            log_path=None,
            frontend_exit_code=0 if source.is_file() else None,
            runtime_seconds=0.0 if source.is_file() else None,
            skipped_reason="" if source.is_file() else "missing_benchmark_input",
        )
    if kind != "yosys_verilog":
        return FrontendResult(
            benchmark=benchmark,
            input_path=None,
            frontend_kind="unsupported",
            frontend_status="unsupported",
            command="",
            log_path=None,
            frontend_exit_code=None,
            runtime_seconds=None,
            skipped_reason="unsupported_benchmark_frontend",
        )

    key = benchmark_key(benchmark)
    frontend_root = output_root / "frontend"
    output_path = frontend_root / "outputs" / f"{key}.blif"
    log_path = frontend_root / "logs" / f"{key}.yosys.log"
    script = render_yosys_verilog_to_blif_script(
        source_path=repo_relative_path(context, source),
        output_path=repo_relative_path(context, output_path),
    )
    command = shlex.join((yosys_bin, "-q", "-p", script))
    if from_existing_logs:
        return load_frontend_result_from_log(
            context=context,
            benchmark=benchmark,
            output_path=output_path,
            log_path=log_path,
            command=command,
        )
    if not source.is_file():
        return FrontendResult(
            benchmark=benchmark,
            input_path=None,
            frontend_kind=kind,
            frontend_status="missing_input",
            command=command,
            log_path=log_path,
            frontend_exit_code=None,
            runtime_seconds=None,
            skipped_reason="missing_benchmark_input",
        )
    return run_yosys_frontend(
        benchmark=benchmark,
        output_path=output_path,
        log_path=log_path,
        yosys_bin=yosys_bin,
        yosys_script=script,
        command=command,
        cwd=context.repo_root,
        timeout_seconds=timeout_seconds,
    )


def render_yosys_verilog_to_blif_script(
    *,
    source_path: Path,
    output_path: Path,
) -> str:
    """Render the deterministic technology-independent Verilog frontend.

    ``hierarchy -auto-top`` lets the public benchmark files retain their own
    top-module names.  ``proc`` and ``memory`` lower behavioral Verilog before
    a generic BLIF is emitted; no Yosys ABC pass is used, so candidate ABC code
    remains the only variable in the subsequent comparison.
    """

    source = shlex.quote(source_path.as_posix())
    output = shlex.quote(output_path.as_posix())
    return "; ".join(
        (
            f"read_verilog {source}",
            "hierarchy -auto-top",
            "proc",
            "opt",
            "memory",
            "opt",
            "techmap",
            "opt",
            "clean",
            f"write_blif {output}",
        )
    )


def run_yosys_frontend(
    *,
    benchmark: Path,
    output_path: Path,
    log_path: Path,
    yosys_bin: str,
    yosys_script: str,
    command: str,
    cwd: Path,
    timeout_seconds: float,
) -> FrontendResult:
    """Run Yosys once and capture an execution-shaped frontend result."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            (yosys_bin, "-q", "-p", yosys_script),
            cwd=cwd,
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
        return FrontendResult(
            benchmark=benchmark,
            input_path=None,
            frontend_kind="yosys_verilog",
            frontend_status="yosys_crash",
            command=command,
            log_path=log_path,
            frontend_exit_code=None,
            runtime_seconds=runtime_seconds,
            skipped_reason=f"yosys_exec_error:{exc.__class__.__name__}",
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
        return FrontendResult(
            benchmark=benchmark,
            input_path=None,
            frontend_kind="yosys_verilog",
            frontend_status="yosys_timeout",
            command=command,
            log_path=log_path,
            frontend_exit_code=None,
            runtime_seconds=runtime_seconds,
            skipped_reason=f"yosys_timeout_after_{timeout_seconds:g}s",
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
    if completed.returncode != 0:
        status = "yosys_failed"
        reason = f"yosys_exit_code={completed.returncode}"
        prepared = None
    elif not output_path.is_file():
        status = "yosys_missing_output"
        reason = "yosys_missing_blif_output"
        prepared = None
    else:
        status = "yosys_pass"
        reason = ""
        prepared = output_path
    return FrontendResult(
        benchmark=benchmark,
        input_path=prepared,
        frontend_kind="yosys_verilog",
        frontend_status=status,
        command=command,
        log_path=log_path,
        frontend_exit_code=completed.returncode,
        runtime_seconds=runtime_seconds,
        skipped_reason=reason,
    )


def load_frontend_result_from_log(
    *,
    context: CycleContext,
    benchmark: Path,
    output_path: Path,
    log_path: Path,
    command: str,
) -> FrontendResult:
    """Reconstruct a Verilog frontend result without launching Yosys."""

    if not log_path.is_file():
        return FrontendResult(
            benchmark=benchmark,
            input_path=None,
            frontend_kind="yosys_verilog",
            frontend_status="missing_frontend_log",
            command=command,
            log_path=log_path,
            frontend_exit_code=None,
            runtime_seconds=None,
            skipped_reason="missing_frontend_log",
        )
    text = log_path.read_text(encoding="utf-8", errors="replace")
    exit_code = parse_log_header_int(text, "return_code")
    runtime_seconds = parse_log_header_float(text, "runtime_seconds")
    if exit_code == 0 and output_path.is_file():
        status = "yosys_pass"
        reason = ""
        prepared = output_path
    elif exit_code == 0:
        status = "yosys_missing_output"
        reason = "yosys_missing_blif_output"
        prepared = None
    else:
        status = "yosys_failed"
        reason = f"yosys_exit_code={exit_code}" if exit_code is not None else "yosys_no_return_code"
        prepared = None
    return FrontendResult(
        benchmark=benchmark,
        input_path=prepared,
        frontend_kind="yosys_verilog",
        frontend_status=status,
        command=command,
        log_path=log_path,
        frontend_exit_code=exit_code,
        runtime_seconds=runtime_seconds,
        skipped_reason=reason,
    )


def write_frontend_summary_csv(
    *,
    context: CycleContext,
    output_root: Path,
    results: Sequence[FrontendResult],
) -> Path:
    path = output_root / "comparison" / "frontend_summary.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FRONTEND_CSV_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "benchmark": _display_path(context, result.benchmark),
                    "frontend_kind": result.frontend_kind,
                    "frontend_status": result.frontend_status,
                    "input_path": (
                        _display_path(context, result.input_path)
                        if result.input_path is not None
                        else ""
                    ),
                    "frontend_exit_code": _empty_if_none(result.frontend_exit_code),
                    "runtime_seconds": _format_float(result.runtime_seconds),
                    "log_path": (
                        _display_path(context, result.log_path)
                        if result.log_path is not None
                        else ""
                    ),
                    "skipped_reason": result.skipped_reason,
                }
            )
    return path


def benchmark_key(benchmark: Path) -> str:
    """Return a stable collision-safe filename component for a source path."""

    normalized = benchmark.as_posix()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    stem = "".join(
        character if character.isalnum() else "_" for character in benchmark.stem
    ).strip("_") or "benchmark"
    return f"{stem}_{digest}"


def _absolute_repo_path(context: CycleContext, path: Path) -> Path:
    return path if path.is_absolute() else context.resolve_repo_path(path.as_posix())


def _display_path(context: CycleContext, path: Path) -> str:
    return repo_relative_path(context, path).as_posix()


def _empty_if_none(value: int | None) -> str:
    return "" if value is None else str(value)


def _format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"
