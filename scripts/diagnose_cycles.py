#!/usr/bin/env python3
"""Collect diagnostic data from completed evolution cycles.

Run this on the remote Linux host after cycle evaluation completes.
Outputs a single JSON bundle that can be rsynced back for local analysis.

Usage:
    python3 -B scripts/diagnose_cycles.py \
        --cycles cycle_001 cycle_002 cycle_003 ... \
        --output diagnostics.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class CycleDiag:
    cycle_id: str
    candidate_id: str
    review_decision: str
    promotion_allowed: bool
    champion_update: bool
    build_status: str
    cec_pass_count: int
    cec_total_count: int
    average_and_improve_pct: float | None
    total_and_delta: int | None
    improved_benchmark_count: int
    regressed_benchmark_count: int
    unchanged_benchmark_count: int
    correctness_backed_rows: int
    benchmark_count: int
    evaluation_benchmark_count: int
    unsupported_benchmark_count: int
    benchmark_frontend: str
    review_reason: str
    review_next_action: str
    previous_patch_target: str
    all_deltas_zero: bool
    qor_delta_rows: list[dict[str, str]] = field(default_factory=list)
    cec_summary_rows: list[dict[str, str]] = field(default_factory=list)


@dataclass
class BatchDiag:
    batch_id: str
    variant_set: str
    summary_path: str
    winner: dict[str, Any] | None
    summary_rows: list[dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect diagnostic data from evolution cycles."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--cycles",
        nargs="*",
        default=None,
        help="Cycle IDs to collect. If omitted, auto-discovers all cycle_* dirs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON bundle to this path. Prints to stdout if omitted.",
    )
    parser.add_argument(
        "--include-qor-details",
        action="store_true",
        default=True,
        help="Include per-benchmark QoR delta rows (default: on).",
    )
    parser.add_argument(
        "--include-cec-details",
        action="store_true",
        default=True,
        help="Include per-benchmark CEC rows (default: on).",
    )
    parser.add_argument(
        "--include-batches",
        action="store_true",
        default=True,
        help="Include batch search summaries if present.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo = args.repo_root.resolve()

    # --- Discover cycles ---
    if args.cycles:
        cycle_ids = list(args.cycles)
    else:
        cycle_ids = _discover_cycles(repo)

    # --- Collect per-cycle diagnostics ---
    cycles: list[CycleDiag] = []
    for cid in cycle_ids:
        diag = _read_cycle(repo, cid, args)
        if diag is not None:
            cycles.append(diag)

    # --- Collect batch diagnostics ---
    batches: list[BatchDiag] = []
    if args.include_batches:
        batches = _read_batches(repo)

    # --- Build summary ---
    bundle = {
        "repo_root": str(repo),
        "cycle_count": len(cycles),
        "batch_count": len(batches),
        "summary": _build_summary(cycles),
        "cycles": [_cycle_as_dict(c) for c in cycles],
        "batches": [asdict(b) for b in batches],
    }

    # --- Output ---
    output_text = json.dumps(bundle, indent=2, sort_keys=True, default=str)

    if args.output:
        args.output.write_text(output_text, encoding="utf-8")
        print(f"Wrote diagnostics to {args.output}")
    else:
        print(output_text)

    # Also print human-readable summary
    _print_human_summary(cycles, batches)

    return 0


# ---------------------------------------------------------------------------
# Cycle reader
# ---------------------------------------------------------------------------


def _read_cycle(
    repo: Path, cycle_id: str, args: argparse.Namespace
) -> CycleDiag | None:
    impl = repo / "experiments" / cycle_id / "impl_compare"
    review_path = impl / "comparison" / "review_decision.json"
    if not review_path.is_file():
        print(f"SKIP {cycle_id}: no review_decision.json", file=sys.stderr)
        return None

    review = _read_json(review_path)
    if not review:
        return None

    qor_rows: list[dict[str, str]] = []
    cec_rows: list[dict[str, str]] = []

    if args.include_qor_details:
        qor_path = impl / "comparison" / "qor_delta.csv"
        qor_rows = _read_csv_dicts(qor_path)

    if args.include_cec_details:
        cec_path = impl / "comparison" / "cec_summary.csv"
        cec_rows = _read_csv_dicts(cec_path)

    patch_target = _read_patch_target(
        impl / "candidate_modified" / "patch.diff"
    )
    assignment = _read_json(
        repo
        / "experiments"
        / cycle_id
        / "agents"
        / "assignments"
        / f"{str(review.get('candidate_id', 'candidate_001'))}.json"
    ) or {}

    nonzero_count = sum(
        1
        for r in qor_rows
        if r.get("and_delta_candidate_minus_baseline", "") not in ("", "0", "0.0")
    )

    return CycleDiag(
        cycle_id=cycle_id,
        candidate_id=str(review.get("candidate_id", "candidate_001")),
        review_decision=str(review.get("decision", "missing")),
        promotion_allowed=bool(review.get("promotion_allowed", False)),
        champion_update=bool(review.get("champion_update", False)),
        build_status=str(review.get("build_status", "missing")),
        cec_pass_count=int(review.get("cec_pass_count", 0)),
        cec_total_count=int(review.get("cec_total_count", 0)),
        average_and_improve_pct=_parse_optional_float(
            review.get("average_and_improve_pct")
        ),
        total_and_delta=_parse_optional_int(
            review.get("total_and_delta_candidate_minus_baseline")
        ),
        improved_benchmark_count=int(
            review.get("improved_benchmark_count", 0)
        ),
        regressed_benchmark_count=int(
            review.get("regressed_benchmark_count", 0)
        ),
        unchanged_benchmark_count=int(
            review.get("unchanged_benchmark_count", 0)
        ),
        correctness_backed_rows=int(
            review.get("correctness_backed_rows", 0)
        ),
        benchmark_count=_count_scope(assignment.get("benchmark_scope", ())),
        evaluation_benchmark_count=_count_scope(
            assignment.get("evaluation_benchmark_scope", ())
        ),
        unsupported_benchmark_count=_count_scope(
            assignment.get("unsupported_benchmark_scope", ())
        ),
        benchmark_frontend=str(assignment.get("benchmark_frontend", "")),
        review_reason=str(review.get("reason", "")),
        review_next_action=str(review.get("next_action", "")),
        previous_patch_target=patch_target,
        all_deltas_zero=(nonzero_count == 0 and len(qor_rows) > 0),
        qor_delta_rows=qor_rows,
        cec_summary_rows=cec_rows,
    )


# ---------------------------------------------------------------------------
# Batch reader
# ---------------------------------------------------------------------------


def _read_batches(repo: Path) -> list[BatchDiag]:
    batches_dir = repo / "experiments" / "batches"
    if not batches_dir.is_dir():
        return []

    results: list[BatchDiag] = []
    for batch_dir in sorted(batches_dir.iterdir()):
        if not batch_dir.is_dir():
            continue
        manifest = _read_json(batch_dir / "manifest.json")
        if manifest is None:
            continue

        summary_rows = _read_csv_dicts(batch_dir / "summary.csv")
        winner = _read_json(batch_dir / "winner.json")

        results.append(
            BatchDiag(
                batch_id=str(manifest.get("batch_id", batch_dir.name)),
                variant_set=str(manifest.get("variant_set", "unknown")),
                summary_path=str(
                    (batch_dir / "summary.csv").relative_to(repo)
                ),
                winner=winner,
                summary_rows=summary_rows,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _build_summary(cycles: list[CycleDiag]) -> dict[str, Any]:
    if not cycles:
        return {"status": "no_cycles_with_review_decision"}

    decisions = [c.review_decision for c in cycles]
    champions = [c for c in cycles if c.champion_update]
    nonzero_qor = [
        c
        for c in cycles
        if not c.all_deltas_zero and c.review_decision in ("REPAIR_QOR", "ACCEPT_FOR_NEXT_CYCLE")
    ]
    build_fail_decisions = frozenset(
        ("REPAIR_VALIDATION", "REPAIR_PATCH", "REPAIR_SMOKE",
         "REPAIR_COMPILE", "REPAIR_EVALUATION", "REPAIR_BUILD")
    )
    repaired = [c for c in cycles
                if c.review_decision in build_fail_decisions
                or c.review_decision == "REJECT_CEC"]

    # Best improvement seen
    best_avg = None
    best_total = None
    best_cycle = None
    for c in cycles:
        if c.average_and_improve_pct is not None:
            if best_avg is None or c.average_and_improve_pct > best_avg:
                best_avg = c.average_and_improve_pct
                best_cycle = c.cycle_id
        if c.total_and_delta is not None:
            if best_total is None or c.total_and_delta < best_total:
                best_total = c.total_and_delta

    return {
        "total_cycles_with_review": len(cycles),
        "champion_count": len(champions),
        "champion_cycles": [c.cycle_id for c in champions],
        "nonzero_qor_count": len(nonzero_qor),
        "nonzero_qor_cycles": [c.cycle_id for c in nonzero_qor],
        "repair_count": len(repaired),
        "repair_cycles": [c.cycle_id for c in repaired],
        "zero_delta_count": sum(1 for c in cycles if c.all_deltas_zero),
        "decision_distribution": {
            d: decisions.count(d) for d in sorted(set(decisions))
        },
        "best_average_and_improve_pct": best_avg,
        "best_average_cycle": best_cycle,
        "best_total_and_delta": best_total,
        "all_cec_pass_rate": (
            f"{sum(1 for c in cycles if c.cec_pass_count == c.cec_total_count and c.cec_total_count > 0)}/{len(cycles)}"
        ),
    }


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


def _print_human_summary(
    cycles: list[CycleDiag], batches: list[BatchDiag]
) -> None:
    print("\n" + "=" * 72, file=sys.stderr)
    print("  DIAGNOSTIC SUMMARY", file=sys.stderr)
    print("=" * 72, file=sys.stderr)

    if not cycles:
        print("  No cycles with review_decision.json found.", file=sys.stderr)
        return

    print(f"\n  {'Cycle':<14} {'Decision':<28} {'CEC':<8} {'Scope':<9} {'ΔAND':>6} {'Avg%':>8} {'I/R/U':>10}  Target", file=sys.stderr)
    print(f"  {'-'*14} {'-'*28} {'-'*8} {'-'*9} {'-'*6} {'-'*8} {'-'*10}  {'-'*40}", file=sys.stderr)

    for c in cycles:
        cec_str = f"{c.cec_pass_count}/{c.cec_total_count}"
        scope_str = (
            f"{c.benchmark_count}/"
            f"{c.evaluation_benchmark_count}/"
            f"{c.unsupported_benchmark_count}"
        )
        delta_str = str(c.total_and_delta) if c.total_and_delta is not None else "N/A"
        avg_str = f"{c.average_and_improve_pct:.2f}%" if c.average_and_improve_pct is not None else "N/A"
        i_r_u = f"{c.improved_benchmark_count}/{c.regressed_benchmark_count}/{c.unchanged_benchmark_count}"
        target = c.previous_patch_target or "(none)"
        if len(target) > 40:
            target = "..." + target[-37:]

        print(
            f"  {c.cycle_id:<14} {c.review_decision:<28} {cec_str:<8} {scope_str:<9} {delta_str:>6} {avg_str:>8} {i_r_u:>10}  {target}",
            file=sys.stderr,
        )

    print(f"\n  Champions: {sum(1 for c in cycles if c.champion_update)}", file=sys.stderr)
    print(f"  Nonzero QoR: {sum(1 for c in cycles if not c.all_deltas_zero and c.review_decision in ('REPAIR_QOR','ACCEPT_FOR_NEXT_CYCLE'))}", file=sys.stderr)
    print(f"  Zero delta: {sum(1 for c in cycles if c.all_deltas_zero)}", file=sys.stderr)

    if batches:
        print(f"\n  Batches: {len(batches)}", file=sys.stderr)
        for b in batches:
            winner_info = ""
            if b.winner and b.winner.get("winner"):
                w = b.winner["winner"]
                winner_info = (
                    f"winner={w.get('variant_id','?')} "
                    f"avg={w.get('average_and_improve_pct','?')}%"
                )
            print(f"    {b.batch_id}: {b.variant_set}  {winner_info}", file=sys.stderr)

    print("\n" + "=" * 72, file=sys.stderr)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _discover_cycles(repo: Path) -> list[str]:
    exp_dir = repo / "experiments"
    if not exp_dir.is_dir():
        return []
    cycles: list[str] = []
    for d in sorted(exp_dir.iterdir()):
        if d.is_dir() and d.name.startswith("cycle_"):
            cycles.append(d.name)
    return cycles


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _count_scope(value: object) -> int:
    if isinstance(value, (str, bytes)):
        return 1 if value else 0
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return 0


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _parse_optional_float(value: object) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_int(value: object) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _read_patch_target(patch_path: Path) -> str:
    if not patch_path.is_file():
        return ""
    text = patch_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("--- a/") or s.startswith("+++ b/"):
            target = s[6:].split("\t", 1)[0].strip()
            if target and target != "/dev/null":
                return target
        if s.startswith("diff --git "):
            parts = s.split()
            if len(parts) >= 4:
                target = parts[3]
                if target.startswith("b/"):
                    target = target[2:]
                if target != "/dev/null":
                    return target
    return ""


def _cycle_as_dict(c: CycleDiag) -> dict[str, Any]:
    d = asdict(c)
    # Truncate large detail lists for readability
    if len(d.get("qor_delta_rows", [])) > 200:
        d["qor_delta_rows_truncated"] = True
        d["qor_delta_rows"] = d["qor_delta_rows"][:200]
    if len(d.get("cec_summary_rows", [])) > 200:
        d["cec_summary_rows_truncated"] = True
        d["cec_summary_rows"] = d["cec_summary_rows"][:200]
    return d


if __name__ == "__main__":
    raise SystemExit(main())
