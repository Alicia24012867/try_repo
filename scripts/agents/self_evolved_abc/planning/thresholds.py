"""Adaptive promotion threshold management.

Implements the paper's "dynamic rule evolution" concept: thresholds start
conservative (ensuring stability), then adapt based on benchmark scope and
cycle evidence.  As the benchmark scope grows, thresholds that made sense for
a 3-design subset may be too strict for 30 designs.

This module is deterministic — no LLM call needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from scripts.agents.self_evolved_abc.planning.evidence import CycleEvidence


@dataclass(frozen=True)
class AdaptiveThresholds:
    """Promotion thresholds adjusted for benchmark scope and cycle history."""

    min_average_and_improve_pct: float
    min_total_and_reduction: int
    min_improved_benchmarks: int

    # Metadata for traceability
    benchmark_count: int
    adjustment_reason: str = ""
    consecutive_qor_stagnation: int = 0
    evolution_phase: str = "conservative"

    def as_dict(self) -> dict[str, float | int]:
        return {
            "min_average_and_improve_pct": self.min_average_and_improve_pct,
            "min_total_and_reduction": self.min_total_and_reduction,
            "min_improved_benchmarks": self.min_improved_benchmarks,
        }


def propose_thresholds(
    *,
    benchmark_count: int,
    previous_evidence: Sequence[CycleEvidence] = (),
    cycle_number: int = 1,
    consecutive_qor_stagnation: int | None = None,
) -> AdaptiveThresholds:
    """Propose promotion thresholds tuned for the current benchmark scope.

    Rules (paper-aligned):
    - ``min_improved_benchmarks``: max(1, min(ceil(benchmark_count * 0.1), 3)).
      For 10 designs → 1; 30 → 3; 70 → 3 (capped).
    - ``min_average_and_improve_pct``: a strong relative-gain alternative to
      the absolute reduction gate; starts at 5 % for ≤10 designs, drops to
      2 % for 30+, then rises back toward 5 % as champions accumulate.
    - ``min_total_and_reduction``: starts at 10 for ≤10 designs, scales slowly
      with design count (not 1:1 — absolute reduction doesn't grow linearly).
    - Early cycles (1–3): more lenient to avoid blocking all candidates.

    Review combines these as: no primary regressions, breadth met, and either
    the relative or absolute magnitude gate met. The paper reports a scalar
    reward plus a detailed vector; it does not prescribe three correlated hard
    gates that must all pass simultaneously.
    """
    reasons: list[str] = []
    stagnation = (
        _consecutive_qor_stagnation(previous_evidence)
        if consecutive_qor_stagnation is None
        else max(0, consecutive_qor_stagnation)
    )
    if cycle_number <= 3 and stagnation < 2:
        evolution_phase = "conservative"
    elif stagnation < 4:
        evolution_phase = "diversify"
    else:
        evolution_phase = "structural"

    # --- min_improved_benchmarks ---
    raw_min_improved = max(1, int(benchmark_count * 0.1 + 0.5))
    min_improved = min(raw_min_improved, 5)
    reasons.append(
        f"min_improved_benchmarks={min_improved} "
        f"(10% of {benchmark_count} designs, capped at 5)"
    )

    # The paper accumulates beneficial changes over many generations and does
    # not impose the reproduction's original 3%-or-15-AND hurdle forever.
    # After three correctness-backed QoR misses, accept any strictly positive,
    # regression-free incremental AND gain; full build, coverage, and CEC stay
    # hard gates in review.py.
    if stagnation >= 3:
        min_improved = 1
        reasons.append(
            "min_improved_benchmarks=1 (controlled relaxation after "
            f"{stagnation} consecutive correctness-backed QoR misses)"
        )

    # --- min_average_and_improve_pct ---
    champion_count = sum(1 for ev in previous_evidence if ev.is_champion)
    if benchmark_count <= 10:
        base_pct = 5.0
    elif benchmark_count <= 30:
        base_pct = 3.0
    else:
        base_pct = 2.0

    if stagnation >= 3:
        avg_pct = 0.0
        reasons.append(
            "min_average_and_improve_pct=0.0% (incremental accumulation phase)"
        )
    # Early-cycle leniency: relax by 40 % in cycles 1-2
    elif cycle_number <= 2 and champion_count == 0:
        avg_pct = base_pct * 0.6
        reasons.append(
            f"min_average_and_improve_pct={avg_pct:.1f}% "
            f"(early-cycle leniency: {base_pct}% × 0.6)"
        )
    # Champion-driven tightening
    elif champion_count >= 3:
        avg_pct = min(base_pct * 1.2, 8.0)
        reasons.append(
            f"min_average_and_improve_pct={avg_pct:.1f}% "
            f"(tightened after {champion_count} champions)"
        )
    else:
        avg_pct = base_pct
        reasons.append(
            f"min_average_and_improve_pct={avg_pct:.1f}% "
            f"(base for {benchmark_count} designs)"
        )

    # --- min_total_and_reduction ---
    if benchmark_count <= 10:
        total_reduction = 10
    elif benchmark_count <= 30:
        total_reduction = 15
    else:
        total_reduction = 20
    if stagnation >= 3:
        total_reduction = 1
    reasons.append(
        f"min_total_and_reduction={total_reduction} "
        f"(for {benchmark_count} designs)"
    )

    return AdaptiveThresholds(
        min_average_and_improve_pct=avg_pct,
        min_total_and_reduction=total_reduction,
        min_improved_benchmarks=min_improved,
        benchmark_count=benchmark_count,
        adjustment_reason="; ".join(reasons),
        consecutive_qor_stagnation=stagnation,
        evolution_phase=evolution_phase,
    )


def _consecutive_qor_stagnation(
    evidence: Sequence[CycleEvidence],
) -> int:
    """Count trailing correctness-backed non-promoting QoR reviews."""

    count = 0
    for item in reversed(tuple(evidence)):
        if item.is_champion:
            break
        if not item.is_repair_qor or not item.all_cec_pass:
            break
        count += 1
    return count
