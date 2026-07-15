#!/usr/bin/env python3
"""Summarize one experiment cycle into CSV tables and run notes.

The script is intentionally conservative: it parses only evidence already
present in a cycle directory and does not rerun ABC or FlowTune. It is meant to
close the P0 baseline step by turning raw logs into reviewable artifacts.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.agents.self_evolved_abc.flow.metrics import (
    parse_last_ps_metrics_file,
    strip_ansi,
)
NORMAL_STAGE_RE = re.compile(
    r"^=+\s+"
    r"(?P<design>\S+)\s+"
    r"(?P<stage>cleanup|vanilla|flowtune search|flowtune apply|done)\s+"
    r"(?P<timestamp>.*?)\s+"
    r"=+$"
)
SKIPPED_RE = re.compile(
    r"^=+\s+(?P<design>\S+)\s+skipped:\s+(?P<reason>.*?)\s+=+$"
)


SUMMARY_FIELDS = (
    "suite",
    "design",
    "status",
    "vanilla_and",
    "vanilla_lev",
    "flowtune_and",
    "flowtune_lev",
    "and_delta",
    "and_improve_pct",
    "lev_delta",
    "elapsed_seconds",
    "vanilla_seconds",
    "flowtune_search_seconds",
    "flowtune_apply_seconds",
    "has_vanilla_aig",
    "has_flowtune_aig",
    "has_flowtune_script",
    "notes",
)
SKIPPED_FIELDS = ("suite", "design", "status", "reason", "source")


@dataclass(frozen=True)
class BatchTiming:
    cleanup: datetime | None = None
    vanilla: datetime | None = None
    flowtune_search: datetime | None = None
    flowtune_apply: datetime | None = None
    done: datetime | None = None

    def duration(self, start: str, end: str) -> int | None:
        start_time = getattr(self, start)
        end_time = getattr(self, end)
        if start_time is None or end_time is None:
            return None
        return int((end_time - start_time).total_seconds())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    cycle_dir = args.cycle_dir.resolve()
    if not cycle_dir.exists():
        print(f"cycle directory does not exist: {cycle_dir}", file=sys.stderr)
        return 2

    logs_dir = cycle_dir / "logs"
    outputs_dir = cycle_dir / "outputs"
    results_dir = cycle_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    summary_rows, skipped_rows = summarize_cycle(logs_dir, outputs_dir)

    write_csv(results_dir / "summary.csv", SUMMARY_FIELDS, summary_rows)
    write_csv(results_dir / "skipped.csv", SKIPPED_FIELDS, skipped_rows)
    (results_dir / "run_notes.md").write_text(
        render_run_notes(cycle_dir, summary_rows, skipped_rows),
        encoding="utf-8",
    )

    complete_count = sum(1 for row in summary_rows if row["status"] == "complete")
    skipped_count = len(skipped_rows)
    print(
        f"wrote {len(summary_rows)} summary rows "
        f"({complete_count} complete, {skipped_count} skipped)"
    )
    print(f"results: {results_dir}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize a cycle's existing logs and outputs."
    )
    parser.add_argument(
        "cycle_dir",
        type=Path,
        help="Experiment cycle directory, for example experiments/cycle_000.",
    )
    return parser.parse_args(argv)


def summarize_cycle(
    logs_dir: Path, outputs_dir: Path
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    skipped = parse_skipped_designs(logs_dir / "batch_epfl.log")
    timings = parse_batch_timings(logs_dir / "batch_epfl.log")
    designs = discover_designs(logs_dir, outputs_dir, skipped)

    summary_rows: list[dict[str, object]] = []
    skipped_rows: list[dict[str, object]] = []
    for design in designs:
        suite = infer_suite(design)
        if design in skipped:
            skipped_rows.append(
                {
                    "suite": suite,
                    "design": design,
                    "status": "skipped",
                    "reason": skipped[design],
                    "source": "logs/batch_epfl.log",
                }
            )

        summary_rows.append(
            summarize_design(
                design=design,
                suite=suite,
                logs_dir=logs_dir,
                outputs_dir=outputs_dir,
                skipped_reason=skipped.get(design),
                timing=timings.get(design, BatchTiming()),
            )
        )

    return summary_rows, skipped_rows


def discover_designs(
    logs_dir: Path, outputs_dir: Path, skipped: Mapping[str, str]
) -> list[str]:
    designs = set(skipped)
    for path in logs_dir.glob("*.vanilla.log"):
        designs.add(path.name[: -len(".vanilla.log")])
    for path in logs_dir.glob("*.flowtune.log"):
        designs.add(path.name[: -len(".flowtune.log")])
    for path in outputs_dir.iterdir() if outputs_dir.exists() else ():
        for suffix in (".vanilla.aig", ".flowtune.aig", ".flowtune.script"):
            if path.name.endswith(suffix):
                designs.add(path.name[: -len(suffix)])
    return sorted(designs)


def summarize_design(
    *,
    design: str,
    suite: str,
    logs_dir: Path,
    outputs_dir: Path,
    skipped_reason: str | None,
    timing: BatchTiming,
) -> dict[str, object]:
    vanilla_metrics = parse_last_ps_metrics_file(logs_dir / f"{design}.vanilla.log")
    flowtune_metrics = parse_last_ps_metrics_file(
        logs_dir / f"{design}.flowtune.log"
    )

    has_vanilla_aig = (outputs_dir / f"{design}.vanilla.aig").exists()
    has_flowtune_aig = (outputs_dir / f"{design}.flowtune.aig").exists()
    has_flowtune_script = (outputs_dir / f"{design}.flowtune.script").exists()

    issues: list[str] = []
    if skipped_reason:
        issues.append(f"skipped: {skipped_reason}")
    if not skipped_reason and vanilla_metrics is None:
        issues.append("missing vanilla ps metrics")
    if not skipped_reason and flowtune_metrics is None:
        issues.append("missing flowtune ps metrics")
    if not skipped_reason and not has_vanilla_aig:
        issues.append("missing vanilla AIG")
    if not skipped_reason and not has_flowtune_aig:
        issues.append("missing FlowTune AIG")
    if not skipped_reason and not has_flowtune_script:
        issues.append("missing FlowTune script")

    status = "skipped" if skipped_reason else ("complete" if not issues else "incomplete")
    vanilla_and = vanilla_metrics.ands if vanilla_metrics else None
    vanilla_lev = vanilla_metrics.lev if vanilla_metrics else None
    flowtune_and = flowtune_metrics.ands if flowtune_metrics else None
    flowtune_lev = flowtune_metrics.lev if flowtune_metrics else None

    and_delta = subtract(vanilla_and, flowtune_and)
    lev_delta = subtract(vanilla_lev, flowtune_lev)
    and_improve_pct = percent_delta(vanilla_and, flowtune_and)

    return {
        "suite": suite,
        "design": design,
        "status": status,
        "vanilla_and": maybe_int(vanilla_and),
        "vanilla_lev": maybe_int(vanilla_lev),
        "flowtune_and": maybe_int(flowtune_and),
        "flowtune_lev": maybe_int(flowtune_lev),
        "and_delta": maybe_int(and_delta),
        "and_improve_pct": maybe_float(and_improve_pct),
        "lev_delta": maybe_int(lev_delta),
        "elapsed_seconds": maybe_int(timing.duration("cleanup", "done")),
        "vanilla_seconds": maybe_int(timing.duration("vanilla", "flowtune_search")),
        "flowtune_search_seconds": maybe_int(
            timing.duration("flowtune_search", "flowtune_apply")
        ),
        "flowtune_apply_seconds": maybe_int(timing.duration("flowtune_apply", "done")),
        "has_vanilla_aig": has_vanilla_aig,
        "has_flowtune_aig": has_flowtune_aig,
        "has_flowtune_script": has_flowtune_script,
        "notes": "; ".join(issues),
    }


def parse_skipped_designs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    skipped: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SKIPPED_RE.match(strip_ansi(line).strip())
        if match:
            skipped[match.group("design")] = match.group("reason")
    return skipped


def parse_batch_timings(path: Path) -> dict[str, BatchTiming]:
    if not path.exists():
        return {}

    raw: dict[str, dict[str, datetime]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = NORMAL_STAGE_RE.match(strip_ansi(line).strip())
        if not match:
            continue

        timestamp = parse_batch_timestamp(match.group("timestamp"))
        if timestamp is None:
            continue

        design = match.group("design")
        stage = match.group("stage").replace(" ", "_")
        raw.setdefault(design, {})[stage] = timestamp

    return {
        design: BatchTiming(
            cleanup=values.get("cleanup"),
            vanilla=values.get("vanilla"),
            flowtune_search=values.get("flowtune_search"),
            flowtune_apply=values.get("flowtune_apply"),
            done=values.get("done"),
        )
        for design, values in raw.items()
    }


def parse_batch_timestamp(value: str) -> datetime | None:
    normalized = re.sub(r"\s+[A-Z]{2,4}\s+", " ", value.strip())
    try:
        return datetime.strptime(normalized, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None


def render_run_notes(
    cycle_dir: Path,
    summary_rows: list[dict[str, object]],
    skipped_rows: list[dict[str, object]],
) -> str:
    complete_rows = [row for row in summary_rows if row["status"] == "complete"]
    incomplete_rows = [row for row in summary_rows if row["status"] == "incomplete"]
    total_vanilla = sum(int(row["vanilla_and"]) for row in complete_rows)
    total_flowtune = sum(int(row["flowtune_and"]) for row in complete_rows)
    weighted_improvement = percent_delta(total_vanilla, total_flowtune)
    mean_improvement = mean(
        float(row["and_improve_pct"])
        for row in complete_rows
        if row["and_improve_pct"] != ""
    )
    depth_changed = sum(1 for row in complete_rows if int(row["lev_delta"]) != 0)

    skipped_lines = (
        [f"- {row['design']}: {row['reason']}" for row in skipped_rows]
        if skipped_rows
        else ["- None."]
    )
    incomplete_lines = (
        [f"- {row['design']}: {row['notes']}" for row in incomplete_rows]
        if incomplete_rows
        else ["- None."]
    )

    return "\n".join(
        (
            f"# {cycle_dir.name} P0 Summary",
            "",
            "## Scope",
            "",
            f"- Cycle directory: `{cycle_dir}`",
            "- Source evidence: existing ABC/FlowTune logs and generated outputs.",
            "- This summary does not rerun ABC, FlowTune, or CEC.",
            "",
            "## Result Counts",
            "",
            f"- Designs discovered: {len(summary_rows)}",
            f"- Complete comparable designs: {len(complete_rows)}",
            f"- Skipped designs: {len(skipped_rows)}",
            f"- Incomplete designs: {len(incomplete_rows)}",
            "",
            "## QoR Summary",
            "",
            f"- Total vanilla ANDs: {total_vanilla}",
            f"- Total FlowTune ANDs: {total_flowtune}",
            f"- Weighted AND improvement: {maybe_float(weighted_improvement)}%",
            f"- Mean per-design AND improvement: {maybe_float(mean_improvement)}%",
            f"- Designs with depth change: {depth_changed}",
            "",
            "## Skipped Designs",
            "",
            *skipped_lines,
            "",
            "## Incomplete Designs",
            "",
            *incomplete_lines,
            "",
            "## Caveats",
            "",
            "- Correctness is not independently established here because no CEC log was present in this cycle.",
            "- QoR values are parsed from ABC `ps` lines in the existing logs.",
            "- Some ABC command lines still mention `outputs_retry`; those are historical command strings from the retry run, while the current artifacts are under `outputs/`.",
            "- Treat this as the cycle 0 / pre-evolution baseline for planning the next small agent cycle.",
            "",
            "## Generated Files",
            "",
            "- `results/summary.csv`",
            "- `results/skipped.csv`",
            "- `results/run_notes.md`",
            "",
        )
    )


def write_csv(path: Path, fields: Iterable[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def infer_suite(design: str) -> str:
    return design.split("_", 1)[0] if "_" in design else "unknown"


def subtract(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left - right


def percent_delta(baseline: int | None, candidate: int | None) -> float | None:
    if baseline in (None, 0) or candidate is None:
        return None
    return 100.0 * (baseline - candidate) / baseline


def mean(values: Iterable[float]) -> float | None:
    values = list(values)
    if not values:
        return None
    return sum(values) / len(values)


def maybe_int(value: int | None) -> int | str:
    return "" if value is None else value


def maybe_float(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
