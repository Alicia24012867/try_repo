"""Promotion thresholds and QoR delta helpers for Flow Agent reviews."""

from __future__ import annotations

import math
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
    parsed_delta_count: int = 0
    parsed_improve_pct_count: int = 0


@dataclass(frozen=True)
class StructuralQorStats:
    """Suite-level AIG size/depth vector used as a physical-QoR proxy.

    The paper's final reward is based on mapped timing and area, with AIG
    structure retained as dense auxiliary feedback.  The small reproduction
    does not yet have the ASAP7 physical flow, so it records the normalized
    node-depth product explicitly as a *structural proxy* instead of silently
    treating AND count as the whole reward.
    """

    total_depth_delta: int | None
    depth_improved_count: int
    depth_regressed_count: int
    depth_unchanged_count: int
    structural_proxy_reward_pct: float | None
    paired_metric_count: int
    parsed_depth_delta_count: int
    max_node_regression_pct: float
    max_depth_regression_pct: float


# Direct multi-objective promotion may tolerate only small per-design
# regressions when the suite aggregate is Pareto-positive.  These are
# guardrails, not substitutes for the full CEC and coverage gates.
MAX_PARETO_NODE_REGRESSION_PCT = 1.0
MAX_PARETO_DEPTH_REGRESSION_PCT = 5.0
MAX_FRONTIER_NODE_REGRESSION_PCT = 5.0
MAX_FRONTIER_DEPTH_REGRESSION_PCT = 20.0


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
    parsed_improve_pct_count = sum(
        1 for row in rows if parse_float(row.get("and_improve_pct")) is not None
    )
    if not parsed:
        return AndDeltaStats(
            total_delta=None,
            improved_count=0,
            regressed_count=0,
            unchanged_count=0,
            parsed_delta_count=0,
            parsed_improve_pct_count=parsed_improve_pct_count,
        )
    return AndDeltaStats(
        total_delta=sum(parsed),
        improved_count=sum(1 for value in parsed if value < 0),
        regressed_count=sum(1 for value in parsed if value > 0),
        unchanged_count=sum(1 for value in parsed if value == 0),
        parsed_delta_count=len(parsed),
        parsed_improve_pct_count=parsed_improve_pct_count,
    )


def structural_qor_stats(
    rows: Sequence[Mapping[str, object]],
) -> StructuralQorStats:
    """Return the detailed node/depth vector and normalized ADP proxy."""

    depth_deltas = [
        parse_int(row.get("depth_delta_candidate_minus_baseline"))
        for row in rows
    ]
    parsed_depth = [value for value in depth_deltas if value is not None]
    log_ratios: list[float] = []
    max_node_regression_pct = 0.0
    max_depth_regression_pct = 0.0
    for row in rows:
        baseline_nodes = parse_float(row.get("baseline_aig_nodes"))
        candidate_nodes = parse_float(row.get("candidate_aig_nodes"))
        baseline_depth = parse_float(row.get("baseline_aig_depth"))
        candidate_depth = parse_float(row.get("candidate_aig_depth"))
        if (
            baseline_nodes is None
            or candidate_nodes is None
            or baseline_depth is None
            or candidate_depth is None
            or baseline_nodes <= 0.0
            or candidate_nodes <= 0.0
            or baseline_depth <= 0.0
            or candidate_depth <= 0.0
        ):
            continue
        node_ratio = candidate_nodes / baseline_nodes
        depth_ratio = candidate_depth / baseline_depth
        log_ratios.append(math.log(node_ratio * depth_ratio))
        max_node_regression_pct = max(
            max_node_regression_pct,
            max(0.0, (node_ratio - 1.0) * 100.0),
        )
        max_depth_regression_pct = max(
            max_depth_regression_pct,
            max(0.0, (depth_ratio - 1.0) * 100.0),
        )

    proxy_reward = None
    if log_ratios:
        geometric_mean_ratio = math.exp(sum(log_ratios) / len(log_ratios))
        proxy_reward = (1.0 - geometric_mean_ratio) * 100.0
    return StructuralQorStats(
        total_depth_delta=(sum(parsed_depth) if parsed_depth else None),
        depth_improved_count=sum(1 for value in parsed_depth if value < 0),
        depth_regressed_count=sum(1 for value in parsed_depth if value > 0),
        depth_unchanged_count=sum(1 for value in parsed_depth if value == 0),
        structural_proxy_reward_pct=proxy_reward,
        paired_metric_count=len(log_ratios),
        parsed_depth_delta_count=len(parsed_depth),
        max_node_regression_pct=max_node_regression_pct,
        max_depth_regression_pct=max_depth_regression_pct,
    )


def meets_structural_pareto_policy(
    *,
    delta_stats: AndDeltaStats,
    structural_stats: StructuralQorStats,
) -> bool:
    """Accept a suite-level size/depth Pareto improvement with guardrails."""

    node_delta = delta_stats.total_delta
    depth_delta = structural_stats.total_depth_delta
    return (
        node_delta is not None
        and depth_delta is not None
        and structural_stats.paired_metric_count > 0
        and node_delta <= 0
        and depth_delta <= 0
        and (node_delta < 0 or depth_delta < 0)
        and structural_stats.structural_proxy_reward_pct is not None
        and structural_stats.structural_proxy_reward_pct > 0.0
        and structural_stats.max_node_regression_pct
        <= MAX_PARETO_NODE_REGRESSION_PCT
        and structural_stats.max_depth_regression_pct
        <= MAX_PARETO_DEPTH_REGRESSION_PCT
    )


def is_structural_frontier_candidate(
    *,
    delta_stats: AndDeltaStats,
    structural_stats: StructuralQorStats,
) -> bool:
    """Keep a correctness-backed partial trade-off for later re-evaluation.

    Frontier retention never updates the baseline.  A cross-subsystem
    combination must become a fresh candidate and repeat build, full CEC, and
    QoR before it can be promoted.
    """

    node_delta = delta_stats.total_delta
    depth_delta = structural_stats.total_depth_delta
    reward = structural_stats.structural_proxy_reward_pct
    has_positive_dimension = (
        (node_delta is not None and node_delta < 0)
        or (depth_delta is not None and depth_delta < 0)
        or (reward is not None and reward > 0.0)
    )
    return (
        structural_stats.paired_metric_count > 0
        and has_positive_dimension
        and structural_stats.max_node_regression_pct
        <= MAX_FRONTIER_NODE_REGRESSION_PCT
        and structural_stats.max_depth_regression_pct
        <= MAX_FRONTIER_DEPTH_REGRESSION_PCT
    )


def meets_promotion_thresholds(
    *,
    avg_and: float | None,
    delta_stats: AndDeltaStats,
    thresholds: PromotionThresholds,
    structural_stats: StructuralQorStats | None = None,
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
    if structural_stats is not None and not structural_regression_guard_passes(
        structural_stats
    ):
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


def structural_regression_guard_passes(
    structural_stats: StructuralQorStats,
) -> bool:
    """Protect scalar area gains from hiding a severe depth/timing proxy loss."""

    reward = structural_stats.structural_proxy_reward_pct
    return (
        reward is not None
        and reward > 0.0
        and structural_stats.max_node_regression_pct
        <= MAX_PARETO_NODE_REGRESSION_PCT
        and structural_stats.max_depth_regression_pct
        <= MAX_PARETO_DEPTH_REGRESSION_PCT
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
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_int(value: object) -> int | None:
    parsed = parse_float(value)
    if parsed is None or not parsed.is_integer():
        return None
    try:
        return int(parsed)
    except (OverflowError, ValueError):
        return None


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
