"""ASAP7 Liberty mapping, gate-sizing, and STA evidence collection.

The structural AIG vector remains useful diagnostic feedback, but it cannot be
substituted for the paper's mapped timing/area measurements.  This module
replays the FlowTune STA-demo sequence against the bundled ASAP7 Liberty file:
``read_lib; map; topo; upsize; dnsize; topo; stime``.

It intentionally does not invent a clock constraint.  When a frozen contract
supplies ``clock_period_ps``, worst slack is derived from the measured critical
path delay.  Otherwise the output is explicitly a critical-path-delay result,
not a falsely labelled WNS result.
"""

from __future__ import annotations

import csv
import json
import math
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.command_io import render_command_log
from scripts.agents.self_evolved_abc.flow.metrics import (
    parse_log_header_float,
    parse_log_header_int,
    strip_ansi,
)
from scripts.agents.self_evolved_abc.flow.verilog_frontend import benchmark_key


ASAP7_QOR_SCHEMA_VERSION = 1
ASAP7_DEFAULT_LIBRARY = "third_party/FlowTune/7nm_lvt_ff.lib"
ASAP7_FLOWTUNE_STA_COMMANDS = (
    "map",
    "topo",
    "upsize",
    "dnsize",
    "topo",
    "stime",
)
ASAP7_QOR_FIELDS = (
    "benchmark",
    "flow_id",
    "cec_status",
    "correctness_backed",
    "baseline_status",
    "candidate_status",
    "baseline_area",
    "candidate_area",
    "area_delta_candidate_minus_baseline",
    "area_improve_pct",
    "baseline_sta_delay_ps",
    "candidate_sta_delay_ps",
    "delay_delta_candidate_minus_baseline_ps",
    "delay_improve_pct",
    "baseline_worst_slack_ps",
    "candidate_worst_slack_ps",
    "worst_slack_delta_candidate_minus_baseline_ps",
    "baseline_adp",
    "candidate_adp",
    "adp_improve_pct",
    "baseline_log_path",
    "candidate_log_path",
    "skipped_reason",
)

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_AREA_RE = re.compile(r"\bArea\s*=\s*(%s)" % _NUMBER, re.IGNORECASE)
_DELAY_RE = re.compile(r"\bDelay\s*=\s*(%s)\s*ps\b" % _NUMBER, re.IGNORECASE)


@dataclass(frozen=True)
class Asap7QorResult:
    benchmark: str
    flow_id: str
    implementation_label: str
    source_aig: Path
    command: str
    log_path: Path
    exit_code: int | None
    area: float | None
    sta_delay_ps: float | None
    worst_slack_ps: float | None
    runtime_seconds: float | None
    status: str
    skipped_reason: str


def default_asap7_qor_config() -> dict[str, object]:
    """Return the frozen, bundled-ASAP7 physical-QoR recipe."""

    return {
        "schema_version": ASAP7_QOR_SCHEMA_VERSION,
        "enabled": True,
        "library_path": ASAP7_DEFAULT_LIBRARY,
        "flowtune_sta_commands": list(ASAP7_FLOWTUNE_STA_COMMANDS),
        "clock_period_ps": None,
    }


def normalize_asap7_qor_config(
    value: object,
    *,
    default_enabled: bool = False,
) -> dict[str, object]:
    """Validate a portable ASAP7 mapping configuration.

    Older assignments that lack this field remain structurally evaluable.  New
    frozen portfolio assignments carry the enabled default explicitly.
    """

    payload = default_asap7_qor_config()
    if value in (None, ""):
        payload["enabled"] = default_enabled
    elif not isinstance(value, Mapping):
        raise ValueError("asap7_qor must be an object")
    else:
        unexpected = set(value) - set(payload)
        if unexpected:
            raise ValueError(
                "unsupported asap7_qor keys: " + ", ".join(sorted(unexpected))
            )
        payload.update(dict(value))
    if not isinstance(payload["enabled"], bool):
        raise ValueError("asap7_qor.enabled must be boolean")
    library_path = str(payload["library_path"] or "").strip()
    path = Path(library_path)
    if not library_path or path.is_absolute() or ".." in path.parts:
        raise ValueError("asap7_qor.library_path must be a repository-relative path")
    payload["library_path"] = path.as_posix()
    commands = payload["flowtune_sta_commands"]
    if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)):
        raise ValueError("asap7_qor.flowtune_sta_commands must be a sequence")
    if list(commands) != list(ASAP7_FLOWTUNE_STA_COMMANDS):
        raise ValueError("asap7_qor must use the frozen FlowTune STA command sequence")
    payload["flowtune_sta_commands"] = list(ASAP7_FLOWTUNE_STA_COMMANDS)
    clock_period = payload["clock_period_ps"]
    if clock_period in (None, ""):
        payload["clock_period_ps"] = None
    else:
        if isinstance(clock_period, bool):
            raise ValueError("asap7_qor.clock_period_ps must be a positive number")
        try:
            parsed_clock = float(clock_period)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "asap7_qor.clock_period_ps must be a positive number"
            ) from exc
        if not math.isfinite(parsed_clock) or parsed_clock <= 0.0:
            raise ValueError("asap7_qor.clock_period_ps must be a positive number")
        payload["clock_period_ps"] = parsed_clock
    payload["schema_version"] = ASAP7_QOR_SCHEMA_VERSION
    return payload


def render_asap7_sta_script(*, aig_path: Path, library_path: Path) -> str:
    """Render the bundled FlowTune ASAP7 mapping/sizing/STA sequence."""

    return "; ".join(
        (
            f"read {aig_path}",
            f"read_lib {library_path}",
            *ASAP7_FLOWTUNE_STA_COMMANDS,
        )
    )


def collect_asap7_qor_result(
    *,
    context: CycleContext,
    output_root: Path,
    benchmark: Path,
    flow_id: str,
    implementation_label: str,
    source_aig: Path,
    abc_bin: str,
    timeout_seconds: float,
    config: Mapping[str, object],
    from_existing_logs: bool,
    cec_backed: bool = True,
) -> Asap7QorResult:
    """Map one CEC-backed AIG using ASAP7 and collect area/delay evidence."""

    normalized = normalize_asap7_qor_config(config)
    design = benchmark_key(benchmark)
    log_path = (
        output_root
        / implementation_label
        / "logs"
        / "asap7"
        / flow_id
        / f"{design}.qor.log"
    )
    try:
        relative_aig = source_aig.resolve().relative_to(context.repo_root)
        library_path = context.repo_root / str(normalized["library_path"])
        relative_library = library_path.resolve().relative_to(context.repo_root)
    except ValueError:
        return _write_failure(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command="asap7 mapping unavailable",
            log_path=log_path,
            runtime_seconds=0.0,
            status="asap7_skipped",
            skipped_reason="asap7_path_outside_repo",
            output="ASAP7_SKIPPED: source AIG or Liberty is outside repository\n",
        )
    script = render_asap7_sta_script(
        aig_path=relative_aig,
        library_path=relative_library,
    )
    command = shlex.join((abc_bin, "-c", script))
    if not bool(normalized["enabled"]):
        return _write_failure(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            runtime_seconds=0.0,
            status="asap7_disabled",
            skipped_reason="asap7_qor_disabled",
            output="ASAP7_DISABLED by frozen evaluation contract\n",
        )
    if not cec_backed:
        return _write_failure(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            runtime_seconds=0.0,
            status="asap7_skipped",
            skipped_reason="physical_qor_requires_cec_pass",
            output="ASAP7_SKIPPED: physical QoR requires a CEC-backed AIG\n",
        )
    if not source_aig.is_file():
        return _write_failure(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            runtime_seconds=0.0,
            status="asap7_skipped",
            skipped_reason="missing_cec_backed_aig",
            output="ASAP7_SKIPPED: missing CEC-backed AIG\n",
        )
    if not library_path.is_file():
        return _write_failure(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            runtime_seconds=0.0,
            status="asap7_skipped",
            skipped_reason="missing_asap7_liberty",
            output=f"ASAP7_SKIPPED: missing Liberty {relative_library}\n",
        )
    if from_existing_logs:
        return load_asap7_qor_result(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            clock_period_ps=normalized["clock_period_ps"],
        )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            (abc_bin, "-c", script),
            cwd=context.repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except OSError as exc:
        return _write_failure(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            runtime_seconds=time.monotonic() - start,
            status="asap7_crash",
            skipped_reason=f"exec_error:{exc.__class__.__name__}",
            output=f"ASAP7_EXEC_ERROR: {exc}\n",
        )
    except subprocess.TimeoutExpired as exc:
        return _write_failure(
            benchmark=benchmark,
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            runtime_seconds=time.monotonic() - start,
            status="asap7_timeout",
            skipped_reason=f"timeout_after_{timeout_seconds:g}s",
            output=(exc.stdout or "") + f"\nASAP7_TIMEOUT after {timeout_seconds:g} seconds\n",
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
    area, delay = parse_asap7_area_delay(output)
    status = "asap7_pass"
    skipped_reason = ""
    if completed.returncode != 0:
        status = "asap7_crash"
        skipped_reason = f"abc_exit_code={completed.returncode}"
    elif area is None or delay is None:
        status = "asap7_unparseable"
        skipped_reason = "missing_asap7_area_or_delay"
    return Asap7QorResult(
        benchmark=str(benchmark),
        flow_id=flow_id,
        implementation_label=implementation_label,
        source_aig=source_aig,
        command=command,
        log_path=log_path,
        exit_code=completed.returncode,
        area=area,
        sta_delay_ps=delay,
        worst_slack_ps=_worst_slack(normalized["clock_period_ps"], delay),
        runtime_seconds=runtime_seconds,
        status=status,
        skipped_reason=skipped_reason,
    )


def load_asap7_qor_result(
    *,
    benchmark: Path,
    flow_id: str,
    implementation_label: str,
    source_aig: Path,
    command: str,
    log_path: Path,
    clock_period_ps: object,
) -> Asap7QorResult:
    """Reload a prior physical run without rerunning mapping/STA."""

    if not log_path.is_file():
        return Asap7QorResult(
            benchmark=str(benchmark),
            flow_id=flow_id,
            implementation_label=implementation_label,
            source_aig=source_aig,
            command=command,
            log_path=log_path,
            exit_code=None,
            area=None,
            sta_delay_ps=None,
            worst_slack_ps=None,
            runtime_seconds=None,
            status="asap7_skipped",
            skipped_reason="missing_asap7_log",
        )
    text = log_path.read_text(encoding="utf-8", errors="replace")
    exit_code = parse_log_header_int(text, "return_code")
    runtime_seconds = parse_log_header_float(text, "runtime_seconds")
    area, delay = parse_asap7_area_delay(text)
    status = "asap7_pass"
    skipped_reason = ""
    if exit_code != 0:
        status = "asap7_crash"
        skipped_reason = f"abc_exit_code={exit_code}"
    elif area is None or delay is None:
        status = "asap7_unparseable"
        skipped_reason = "missing_asap7_area_or_delay"
    return Asap7QorResult(
        benchmark=str(benchmark),
        flow_id=flow_id,
        implementation_label=implementation_label,
        source_aig=source_aig,
        command=command,
        log_path=log_path,
        exit_code=exit_code,
        area=area,
        sta_delay_ps=delay,
        worst_slack_ps=_worst_slack(clock_period_ps, delay),
        runtime_seconds=runtime_seconds,
        status=status,
        skipped_reason=skipped_reason,
    )


def parse_asap7_area_delay(text: str) -> tuple[float | None, float | None]:
    """Parse final SCL ``stime`` area and critical path delay in ps."""

    cleaned = strip_ansi(text)
    areas = [float(item) for item in _AREA_RE.findall(cleaned)]
    delays = [float(item) for item in _DELAY_RE.findall(cleaned)]
    area = areas[-1] if areas else None
    delay = delays[-1] if delays else None
    if area is not None and (not math.isfinite(area) or area <= 0.0):
        area = None
    if delay is not None and (not math.isfinite(delay) or delay <= 0.0):
        delay = None
    return area, delay


def build_asap7_qor_rows(
    baseline_results: Sequence[Asap7QorResult],
    candidate_results: Sequence[Asap7QorResult],
    cec_statuses: Sequence[str],
    *,
    repo_root: Path,
) -> list[dict[str, object]]:
    """Pair baseline/candidate physical evidence with its CEC status."""

    rows: list[dict[str, object]] = []
    for baseline, candidate, cec_status in zip(
        baseline_results, candidate_results, cec_statuses
    ):
        backed = (
            cec_status == "cec_pass"
            and baseline.status == "asap7_pass"
            and candidate.status == "asap7_pass"
        )
        reasons = [
            item
            for item in (
                "" if cec_status == "cec_pass" else f"cec:{cec_status}",
                baseline.skipped_reason,
                candidate.skipped_reason,
            )
            if item
        ]
        baseline_adp = _adp(baseline.area, baseline.sta_delay_ps)
        candidate_adp = _adp(candidate.area, candidate.sta_delay_ps)
        rows.append(
            {
                "benchmark": baseline.benchmark,
                "flow_id": baseline.flow_id,
                "cec_status": cec_status,
                "correctness_backed": backed,
                "baseline_status": baseline.status,
                "candidate_status": candidate.status,
                "baseline_area": _format(baseline.area),
                "candidate_area": _format(candidate.area),
                "area_delta_candidate_minus_baseline": _format_delta(
                    candidate.area, baseline.area, backed
                ),
                "area_improve_pct": _format_percent(
                    baseline.area, candidate.area, backed
                ),
                "baseline_sta_delay_ps": _format(baseline.sta_delay_ps),
                "candidate_sta_delay_ps": _format(candidate.sta_delay_ps),
                "delay_delta_candidate_minus_baseline_ps": _format_delta(
                    candidate.sta_delay_ps, baseline.sta_delay_ps, backed
                ),
                "delay_improve_pct": _format_percent(
                    baseline.sta_delay_ps, candidate.sta_delay_ps, backed
                ),
                "baseline_worst_slack_ps": _format(baseline.worst_slack_ps),
                "candidate_worst_slack_ps": _format(candidate.worst_slack_ps),
                "worst_slack_delta_candidate_minus_baseline_ps": _format_delta(
                    candidate.worst_slack_ps, baseline.worst_slack_ps, backed
                ),
                "baseline_adp": _format(baseline_adp),
                "candidate_adp": _format(candidate_adp),
                "adp_improve_pct": _format_percent(
                    baseline_adp, candidate_adp, backed
                ),
                "baseline_log_path": _display_path(baseline.log_path, repo_root),
                "candidate_log_path": _display_path(candidate.log_path, repo_root),
                "skipped_reason": "; ".join(sorted(set(reasons))),
            }
        )
    return rows


def write_asap7_qor_csv(output_root: Path, rows: Sequence[Mapping[str, object]]) -> Path:
    path = output_root / "comparison" / "asap7_qor_by_flow.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ASAP7_QOR_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_asap7_qor_summary(
    output_root: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    config: Mapping[str, object],
    static_flow_ids: Sequence[str],
) -> Path:
    """Persist normalized physical QoR ratios for paper-table comparison."""

    normalized = normalize_asap7_qor_config(config)
    backed = [row for row in rows if row.get("correctness_backed") is True]
    static_ids = set(static_flow_ids)
    all_static_rows = [row for row in rows if row.get("flow_id") in static_ids]
    static = [row for row in backed if row.get("flow_id") in static_ids]
    summary = {
        "schema_version": ASAP7_QOR_SCHEMA_VERSION,
        "enabled": bool(normalized["enabled"]),
        "library_path": normalized["library_path"],
        "flowtune_sta_commands": list(ASAP7_FLOWTUNE_STA_COMMANDS),
        "clock_period_ps": normalized["clock_period_ps"],
        "timing_metric": (
            "worst_slack_ps"
            if normalized["clock_period_ps"] is not None
            else "critical_path_delay_ps_no_clock_constraint"
        ),
        "rows_total": len(rows),
        "rows_correctness_backed": len(backed),
        "paper_static_rows_correctness_backed": len(static),
        "area_geomean_ratio_candidate_over_baseline": _geomean_ratio(
            backed, "baseline_area", "candidate_area"
        ),
        "delay_geomean_ratio_candidate_over_baseline": _geomean_ratio(
            backed, "baseline_sta_delay_ps", "candidate_sta_delay_ps"
        ),
        "adp_geomean_ratio_candidate_over_baseline": _geomean_ratio(
            backed, "baseline_adp", "candidate_adp"
        ),
        "table_comparable": bool(normalized["enabled"])
        and normalized["clock_period_ps"] is not None
        and len(static) == len(all_static_rows)
        and bool(all_static_rows),
        "table_comparability_note": (
            "ASAP7 Liberty mapping, post-sizing area, and STA WNS are present."
            if normalized["clock_period_ps"] is not None
            else "ASAP7 area and critical-path delay are present, but WNS is not "
            "reported because the frozen contract has no clock period."
        ),
    }
    path = output_root / "comparison" / "asap7_qor_summary.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_failure(
    *,
    benchmark: Path,
    flow_id: str,
    implementation_label: str,
    source_aig: Path,
    command: str,
    log_path: Path,
    runtime_seconds: float,
    status: str,
    skipped_reason: str,
    output: str,
) -> Asap7QorResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        render_command_log(
            command=command,
            return_code=None,
            runtime_seconds=runtime_seconds,
            output=output,
        ),
        encoding="utf-8",
    )
    return Asap7QorResult(
        benchmark=str(benchmark),
        flow_id=flow_id,
        implementation_label=implementation_label,
        source_aig=source_aig,
        command=command,
        log_path=log_path,
        exit_code=None,
        area=None,
        sta_delay_ps=None,
        worst_slack_ps=None,
        runtime_seconds=runtime_seconds,
        status=status,
        skipped_reason=skipped_reason,
    )


def _worst_slack(clock_period_ps: object, delay: float | None) -> float | None:
    if clock_period_ps is None or delay is None:
        return None
    return float(clock_period_ps) - delay


def _adp(area: float | None, delay: float | None) -> float | None:
    if area is None or delay is None:
        return None
    return area * delay


def _format(value: float | None) -> str:
    return "" if value is None else f"{value:.12g}"


def _format_delta(candidate: float | None, baseline: float | None, backed: bool) -> str:
    if not backed or candidate is None or baseline is None:
        return ""
    return _format(candidate - baseline)


def _format_percent(baseline: float | None, candidate: float | None, backed: bool) -> str:
    if not backed or baseline is None or candidate is None or baseline <= 0.0:
        return ""
    return _format((1.0 - candidate / baseline) * 100.0)


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _geomean_ratio(
    rows: Sequence[Mapping[str, object]],
    baseline_key: str,
    candidate_key: str,
) -> float | None:
    ratios: list[float] = []
    for row in rows:
        try:
            baseline = float(row.get(baseline_key, ""))
            candidate = float(row.get(candidate_key, ""))
        except (TypeError, ValueError):
            continue
        if baseline > 0.0 and candidate > 0.0:
            ratios.append(candidate / baseline)
    if not ratios:
        return None
    return math.exp(sum(math.log(value) for value in ratios) / len(ratios))
