"""Structured evidence reader for completed Flow Agent cycles.

Reads review_decision.json, qor_delta.csv, cec_summary.csv, and
impl_compare_summary.md from a cycle's impl_compare directory and
returns a rich ``CycleEvidence`` dataclass for the planning engine.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.agents.self_evolved_abc.workflow.artifacts import (
    LEGACY_CYCLE_LAYOUT,
    implementation_root_for,
)


@dataclass(frozen=True)
class CycleEvidence:
    """Structured summary of one completed cycle's evaluation artifacts."""

    cycle_id: str
    candidate_id: str

    # Review gate
    review_decision: str  # ACCEPT_FOR_NEXT_CYCLE | REPAIR_QOR | REJECT_CEC | ...
    promotion_allowed: bool
    champion_update: bool

    # Build
    build_status: str

    # CEC
    cec_pass_count: int
    cec_total_count: int
    all_cec_pass: bool

    # QoR
    average_and_improve_pct: float | None
    total_and_delta: int | None
    improved_benchmark_count: int
    regressed_benchmark_count: int
    unchanged_benchmark_count: int
    correctness_backed_rows: int

    # Per-benchmark detail (from qor_delta.csv)
    per_benchmark: tuple[BenchmarkDelta, ...] = ()

    # Thresholds used in the review
    min_average_and_improve_pct: float = 0.0
    min_total_and_reduction: int = 0
    min_improved_benchmarks: int = 0

    # Human-readable reason from review
    review_reason: str = ""
    review_next_action: str = ""

    # Source patch target (from review context or diff)
    previous_patch_target: str = ""

    # Whether all structural deltas were zero
    all_deltas_zero: bool = False

    # Multi-objective structural proxy fields (optional for older reviews)
    total_depth_delta: int | None = None
    structural_proxy_reward_pct: float | None = None
    retained_for_synergy: bool = False
    promotion_basis: str = ""

    @property
    def is_champion(self) -> bool:
        return self.review_decision == "ACCEPT_FOR_NEXT_CYCLE"

    @property
    def is_repair_qor(self) -> bool:
        return self.review_decision in ("REPAIR_QOR", "RETAIN_FOR_SYNERGY")

    @property
    def is_cec_fail(self) -> bool:
        return self.review_decision == "REJECT_CEC"

    @property
    def is_build_fail(self) -> bool:
        return self.review_decision in (
            "CODING_INFRASTRUCTURE_FAILURE",
            "REPAIR_VALIDATION",
            "REPAIR_PATCH",
            "REPAIR_SMOKE",
            "REPAIR_COMPILE",
            "REPAIR_EVALUATION",
            "REPAIR_BUILD",
        )

    @property
    def nonzero_benchmarks(self) -> tuple[BenchmarkDelta, ...]:
        """Benchmarks where the candidate actually changed something."""
        return tuple(
            bm
            for bm in self.per_benchmark
            if bm.and_delta is not None and bm.and_delta != 0
        )

    @property
    def improved_benchmarks(self) -> tuple[BenchmarkDelta, ...]:
        return tuple(bm for bm in self.per_benchmark if bm.is_improved)

    @property
    def regressed_benchmarks(self) -> tuple[BenchmarkDelta, ...]:
        return tuple(bm for bm in self.per_benchmark if bm.is_regressed)


@dataclass(frozen=True)
class BenchmarkDelta:
    """One row from qor_delta.csv."""

    benchmark: str
    cec_status: str
    correctness_backed: bool
    baseline_and: int | None
    candidate_and: int | None
    and_delta: int | None
    and_improve_pct: float | None
    baseline_depth: int | None
    candidate_depth: int | None
    depth_delta: int | None
    skipped_reason: str = ""

    @property
    def is_improved(self) -> bool:
        return (
            self.correctness_backed
            and self.and_delta is not None
            and self.and_delta < 0
        )

    @property
    def is_regressed(self) -> bool:
        return (
            self.correctness_backed
            and self.and_delta is not None
            and self.and_delta > 0
        )

    @property
    def is_unchanged(self) -> bool:
        return (
            self.correctness_backed
            and self.and_delta is not None
            and self.and_delta == 0
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_cycle_evidence(
    repo_root: Path,
    cycle_id: str,
    *,
    candidate_id: str = "candidate_001",
    artifact_layout: str = LEGACY_CYCLE_LAYOUT,
) -> CycleEvidence | None:
    """Read structured evidence from one completed cycle's impl_compare directory.

    Returns None when the review_decision.json is missing (cycle not yet evaluated).
    """
    impl_dir = implementation_root_for(
        repo_root=repo_root,
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        layout=artifact_layout,
    )
    review_path = impl_dir / "comparison" / "review_decision.json"
    if not review_path.is_file():
        return None

    review = _read_json(review_path)
    if not review:
        return None

    qor_rows = _read_qor_delta(impl_dir / "comparison" / "qor_delta.csv")

    # Detect zero-delta signal
    nonzero_deltas = [
        row
        for row in qor_rows
        if row.and_delta not in (None, 0) or row.depth_delta not in (None, 0)
    ]

    # Read previous patch target
    patch_target = _read_patch_target(
        impl_dir / "candidate_modified" / "patch.diff"
    )

    return CycleEvidence(
        cycle_id=cycle_id,
        candidate_id=candidate_id,
        review_decision=str(review.get("decision", "missing")),
        promotion_allowed=bool(review.get("promotion_allowed", False)),
        champion_update=bool(review.get("champion_update", False)),
        build_status=str(review.get("build_status", "missing")),
        cec_pass_count=int(review.get("cec_pass_count", 0)),
        cec_total_count=int(review.get("cec_total_count", 0)),
        all_cec_pass=(
            int(review.get("cec_pass_count", 0)) == int(review.get("cec_total_count", 1))
            and int(review.get("cec_total_count", 0)) > 0
        ),
        average_and_improve_pct=_parse_optional_float(
            review.get("average_and_improve_pct")
        ),
        total_and_delta=_parse_optional_int(
            review.get("total_and_delta_candidate_minus_baseline")
        ),
        improved_benchmark_count=int(review.get("improved_benchmark_count", 0)),
        regressed_benchmark_count=int(review.get("regressed_benchmark_count", 0)),
        unchanged_benchmark_count=int(review.get("unchanged_benchmark_count", 0)),
        correctness_backed_rows=int(review.get("correctness_backed_rows", 0)),
        per_benchmark=tuple(qor_rows),
        min_average_and_improve_pct=float(
            review.get("min_average_and_improve_pct", 0)
        ),
        min_total_and_reduction=int(review.get("min_total_and_reduction", 0)),
        min_improved_benchmarks=int(review.get("min_improved_benchmarks", 0)),
        review_reason=str(review.get("reason", "")),
        review_next_action=str(review.get("next_action", "")),
        previous_patch_target=patch_target,
        all_deltas_zero=len(nonzero_deltas) == 0 and len(qor_rows) > 0,
        total_depth_delta=_parse_optional_int(
            review.get("total_depth_delta_candidate_minus_baseline")
        ),
        structural_proxy_reward_pct=_parse_optional_float(
            review.get("structural_proxy_reward_pct")
        ),
        retained_for_synergy=bool(
            review.get("retained_for_synergy", False)
        ),
        promotion_basis=str(review.get("promotion_basis", "")),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_qor_delta(path: Path) -> list[BenchmarkDelta]:
    if not path.is_file():
        return []
    rows: list[BenchmarkDelta] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            rows.append(
                BenchmarkDelta(
                    benchmark=row.get("benchmark", ""),
                    cec_status=row.get("cec_status", ""),
                    correctness_backed=str(
                        row.get("correctness_backed", "")
                    ).lower()
                    == "true",
                    baseline_and=_parse_optional_int(row.get("baseline_aig_nodes")),
                    candidate_and=_parse_optional_int(
                        row.get("candidate_aig_nodes")
                    ),
                    and_delta=_parse_optional_int(
                        row.get("and_delta_candidate_minus_baseline")
                    ),
                    and_improve_pct=_parse_optional_float(
                        row.get("and_improve_pct")
                    ),
                    baseline_depth=_parse_optional_int(
                        row.get("baseline_aig_depth")
                    ),
                    candidate_depth=_parse_optional_int(
                        row.get("candidate_aig_depth")
                    ),
                    depth_delta=_parse_optional_int(
                        row.get("depth_delta_candidate_minus_baseline")
                    ),
                    skipped_reason=row.get("skipped_reason", ""),
                )
            )
    return rows


def _read_patch_target(patch_path: Path) -> str:
    """Extract the target file from a unified diff."""
    if not patch_path.is_file():
        return ""
    text = patch_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--- a/") or stripped.startswith("+++ b/"):
            target = stripped[6:].split("\t", 1)[0].strip()
            if target and target != "/dev/null":
                return target
        if stripped.startswith("diff --git "):
            parts = stripped.split()
            if len(parts) >= 4:
                target = parts[3]
                if target.startswith("b/"):
                    target = target[2:]
                if target != "/dev/null":
                    return target
    return ""


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
