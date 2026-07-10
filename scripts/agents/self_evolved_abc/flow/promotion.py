"""Promotion thresholds and QoR delta helpers for Flow Agent reviews."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PromotionThresholds:
    min_average_and_improve_pct: float = 3.0
    min_total_and_reduction: int = 10
    min_improved_benchmarks: int = 2

    def as_dict(self) -> dict[str, float | int]:
        return {
            "min_average_and_improve_pct": self.min_average_and_improve_pct,
            "min_total_and_reduction": self.min_total_and_reduction,
            "min_improved_benchmarks": self.min_improved_benchmarks,
        }


@dataclass(frozen=True)
class AndDeltaStats:
    total_delta: int | None
    improved_count: int
    regressed_count: int
    unchanged_count: int


DEFAULT_PROMOTION_THRESHOLDS = PromotionThresholds()


def normalize_promotion_thresholds(raw: object) -> PromotionThresholds:
    """Return configured promotion thresholds with paper-safe defaults."""

    values = raw if isinstance(raw, Mapping) else {}
    return PromotionThresholds(
        min_average_and_improve_pct=_threshold_float(
            values.get("min_average_and_improve_pct"),
            DEFAULT_PROMOTION_THRESHOLDS.min_average_and_improve_pct,
        ),
        min_total_and_reduction=_threshold_int(
            values.get("min_total_and_reduction"),
            DEFAULT_PROMOTION_THRESHOLDS.min_total_and_reduction,
        ),
        min_improved_benchmarks=_threshold_int(
            values.get("min_improved_benchmarks"),
            DEFAULT_PROMOTION_THRESHOLDS.min_improved_benchmarks,
        ),
    )


def average(values: Iterable[float | None]) -> float | None:
    parsed = [value for value in values if value is not None]
    if not parsed:
        return None
    return sum(parsed) / len(parsed)


def and_delta_stats(rows: Sequence[Mapping[str, object]]) -> AndDeltaStats:
    deltas = [parse_int(row.get("and_delta_candidate_minus_baseline")) for row in rows]
    parsed = [value for value in deltas if value is not None]
    if not parsed:
        return AndDeltaStats(
            total_delta=None,
            improved_count=0,
            regressed_count=0,
            unchanged_count=0,
        )
    return AndDeltaStats(
        total_delta=sum(parsed),
        improved_count=sum(1 for value in parsed if value < 0),
        regressed_count=sum(1 for value in parsed if value > 0),
        unchanged_count=sum(1 for value in parsed if value == 0),
    )


def meets_promotion_thresholds(
    *,
    avg_and: float | None,
    delta_stats: AndDeltaStats,
    thresholds: PromotionThresholds,
) -> bool:
    """Return whether a candidate is a meaningful Pareto-safe improvement.

    The paper feeds a scalar reward and a detailed QoR vector back to the
    planner.  Percentage and absolute AND reduction are two views of the same
    area objective, so requiring both badly over-constrains incremental source
    evolution.  Breadth and no-regression remain hard safeguards; either
    magnitude threshold may establish a meaningful gain.
    """

    if avg_and is None or delta_stats.total_delta is None:
        return False
    return (
        delta_stats.total_delta < 0
        and delta_stats.regressed_count == 0
        and delta_stats.improved_count >= thresholds.min_improved_benchmarks
        and (
            avg_and >= thresholds.min_average_and_improve_pct
            or -delta_stats.total_delta >= thresholds.min_total_and_reduction
        )
    )


def scalar_and_reward(delta_stats: AndDeltaStats) -> int | None:
    """Return the scalar area reward used for planner feedback.

    Positive values are net AND reductions relative to the incumbent; negative
    values are regressions.  The full per-design vector remains authoritative
    for the no-regression and breadth gates.
    """

    if delta_stats.total_delta is None:
        return None
    return -delta_stats.total_delta


def threshold_prompt_text(thresholds: PromotionThresholds) -> str:
    return (
        "Champion promotion requires zero AND-regressed rows, improved benchmark "
        f"rows >= {thresholds.min_improved_benchmarks}, and either average AND "
        f"improvement >= {thresholds.min_average_and_improve_pct}% or total AND "
        f"reduction >= {thresholds.min_total_and_reduction}."
    )


def parse_float(value: object) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_int(value: object) -> int | None:
    parsed = parse_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _threshold_float(value: object, default: float) -> float:
    parsed = parse_float(value)
    return default if parsed is None else parsed


def _threshold_int(value: object, default: int) -> int:
    parsed = parse_int(value)
    return default if parsed is None else parsed


def format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def format_optional_int(value: int | None) -> str:
    return "" if value is None else str(value)
