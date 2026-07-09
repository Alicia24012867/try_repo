"""Deterministic Planning Engine for Flow Agent self-evolution.

Orchestrates: read evidence → reconstruct history → select strategy →
propose thresholds → generate next-cycle assignment updates.

History is reconstructed from previous cycles' ``_planning_meta`` fields
in their assignment JSONs, so the engine can track which commands were
tried and how many champions were promoted across multiple ``next_cycle.py``
invocations (which each create a fresh ``PlanningEngine`` instance).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_SOURCE_TOUCHPOINTS,
    FLOWTUNE_ABCI_SCOPE,
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
)
from scripts.agents.self_evolved_abc.planning.evidence import (
    CycleEvidence,
    read_cycle_evidence,
)
from scripts.agents.self_evolved_abc.planning.strategy import (
    Strategy,
    select_strategy,
)
from scripts.agents.self_evolved_abc.planning.thresholds import (
    AdaptiveThresholds,
    propose_thresholds,
)


@dataclass
class PlanningResult:
    """Output of one planning cycle."""

    cycle_id: str
    next_cycle_id: str
    strategy: Strategy
    thresholds: AdaptiveThresholds
    hypothesis: str
    history: list[CycleEvidence] = field(default_factory=list)
    previous_strategies: list[Strategy] = field(default_factory=list)


class PlanningEngine:
    """Deterministic planning engine for Flow Agent self-evolution.

    Reads completed cycle evidence, reconstructs cross-cycle history from
    persisted ``_planning_meta``, selects a strategy, proposes adaptive
    thresholds, and generates a concrete planner hypothesis.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, previous_cycle_id: str) -> PlanningResult | None:
        """Plan the next cycle based on the previous cycle's evidence.

        Reconstructs cross-cycle history by scanning earlier cycles'
        ``_planning_meta`` and review decisions, so repeated zero-delta
        cycles are correctly detected.
        """
        evidence = read_cycle_evidence(
            self._repo_root, previous_cycle_id
        )

        next_cycle_id = _increment_cycle_id(previous_cycle_id)
        cycle_number = _cycle_number(next_cycle_id)

        # Reconstruct cross-cycle history from persisted data
        history = _reconstruct_evidence_history(
            self._repo_root, previous_cycle_id
        )
        if evidence is not None:
            history.append(evidence)

        strategies = _reconstruct_strategy_history(
            self._repo_root, previous_cycle_id
        )

        benchmark_count = (
            len(evidence.per_benchmark)
            if evidence is not None and evidence.per_benchmark
            else 30
        )

        # --- Thresholds ---
        thresholds = propose_thresholds(
            benchmark_count=benchmark_count,
            previous_evidence=tuple(history),
            cycle_number=cycle_number,
        )

        # --- Strategy ---
        strategy = select_strategy(
            evidence,
            previous_strategies=tuple(strategies),
            cycle_number=cycle_number,
            benchmark_count=benchmark_count,
        )
        strategies.append(strategy)

        # --- Hypothesis ---
        hypothesis = self._build_hypothesis(
            evidence=evidence,
            strategy=strategy,
            thresholds=thresholds,
            cycle_number=cycle_number,
        )

        return PlanningResult(
            cycle_id=previous_cycle_id,
            next_cycle_id=next_cycle_id,
            strategy=strategy,
            thresholds=thresholds,
            hypothesis=hypothesis,
            history=history,
            previous_strategies=strategies,
        )

    def plan_multi(
        self, start_cycle: str, end_cycle: str
    ) -> list[PlanningResult]:
        """Plan across a range of already-completed cycles.

        Reads each cycle's evidence in order, reconstructing history so
        later plans can learn from earlier outcomes.
        """
        results: list[PlanningResult] = []
        current = start_cycle
        while True:
            result = self.plan(current)
            if result is None:
                break
            results.append(result)
            if current == end_cycle:
                break
            current = result.next_cycle_id
        return results

    def next_assignment_updates(
        self, result: PlanningResult
    ) -> dict[str, object]:
        """Build the fields to merge into a next-cycle assignment."""
        strategy = result.strategy
        thresholds = result.thresholds

        return {
            "planner_hypothesis": result.hypothesis,
            "promotion_thresholds": thresholds.as_dict(),
            "discouraged_patch_targets": list(strategy.discouraged_targets),
            "evaluation_flow_commands": list(DEFAULT_EVAL_FLOW_COMMANDS),
            "flow_source_touchpoints": dict(FLOW_SOURCE_TOUCHPOINTS),
            "source_patch_allowed_roots": [
                FLOWTUNE_SOURCE_SCOPE_PRIMARY,
                FLOWTUNE_ABCI_SCOPE,
            ],
            "_planning_meta": {
                "engine": "deterministic",
                "task_type": strategy.task_type,
                "target_command": strategy.target_command,
                "target_source_dir": strategy.target_source_dir,
                "target_parameter_kind": strategy.target_parameter_kind,
                "should_skip_llm": strategy.should_skip_llm,
                "should_relax_thresholds": strategy.should_relax_thresholds,
                "threshold_rationale": thresholds.adjustment_reason,
                "discouraged_targets": list(strategy.discouraged_targets),
            },
        }

    # ------------------------------------------------------------------
    # Hypothesis builder
    # ------------------------------------------------------------------

    def _build_hypothesis(
        self,
        *,
        evidence: CycleEvidence | None,
        strategy: Strategy,
        thresholds: AdaptiveThresholds,
        cycle_number: int,
    ) -> str:
        parts: list[str] = []

        # --- Context: what happened last cycle ---
        if evidence is not None:
            parts.append(
                f"Previous cycle {evidence.cycle_id} review decision: "
                f"{evidence.review_decision}."
            )
            if evidence.review_reason:
                parts.append(f"Review reason: {evidence.review_reason}")
            if evidence.average_and_improve_pct is not None:
                parts.append(
                    f"Average AND improvement: "
                    f"{evidence.average_and_improve_pct:.2f}%."
                )
            parts.append(
                f"Improved/regressed/unchanged benchmarks: "
                f"{evidence.improved_benchmark_count}/"
                f"{evidence.regressed_benchmark_count}/"
                f"{evidence.unchanged_benchmark_count}."
            )
            if evidence.all_deltas_zero:
                parts.append(
                    "ALL benchmarks had ZERO AND-node change — the patch "
                    "did NOT execute at runtime. Check: was the changed code "
                    "guarded by a condition that is never true? Was the "
                    "changed constant overridden before use?"
                )
        else:
            parts.append(
                "No previous cycle evidence is available. This is the first "
                "Flow Agent source-patch cycle."
            )

        # --- Strategy guidance ---
        parts.append(
            f"Strategy: {strategy.task_type}. "
            f"Target command: `{strategy.target_command}`. "
            f"Rationale: {strategy.rationale}"
        )

        if strategy.should_skip_llm:
            parts.append(
                "RECOMMENDATION: Skip this LLM cycle. Run batch_search "
                f"with --variant-set flow_wide targeting `{strategy.target_command}` "
                "first to find productive parameter ranges, then feed the "
                "batch winner back to the Flow Agent."
            )

        # --- Threshold context ---
        parts.append(
            f"Promotion thresholds for next champion: "
            f"avg AND improvement >= {thresholds.min_average_and_improve_pct:.1f}%, "
            f"total AND reduction >= {thresholds.min_total_and_reduction}, "
            f"improved benchmarks >= {thresholds.min_improved_benchmarks}. "
            f"({thresholds.adjustment_reason})"
        )

        # --- Discouraged targets ---
        if strategy.discouraged_targets:
            parts.append(
                "Discouraged patch targets (produced weak/zero QoR): "
                + ", ".join(strategy.discouraged_targets)
                + ". Do NOT edit these files again unless the new patch "
                "changes a different function or parameter."
            )

        # --- Specific action ---
        parts.append(strategy.hypothesis_template)

        return "\n".join(parts)


# ------------------------------------------------------------------
# Cross-cycle history reconstruction
# ------------------------------------------------------------------


def _reconstruct_evidence_history(
    repo_root: Path,
    up_to_cycle: str,
) -> list[CycleEvidence]:
    """Read review decisions from all prior cycles to count champions."""
    history: list[CycleEvidence] = []
    cycle_num = _cycle_number(up_to_cycle)
    # Scan cycles before the current one
    for num in range(1, cycle_num):
        prior = _format_cycle_id(up_to_cycle, num)
        ev = read_cycle_evidence(repo_root, prior)
        if ev is not None:
            history.append(ev)
    return history


def _reconstruct_strategy_history(
    repo_root: Path,
    up_to_cycle: str,
) -> list[Strategy]:
    """Read _planning_meta from all prior cycles to know which commands were tried."""
    strategies: list[Strategy] = []
    cycle_num = _cycle_number(up_to_cycle)
    for num in range(1, cycle_num):
        prior = _format_cycle_id(up_to_cycle, num)
        meta = _read_planning_meta(repo_root, prior)
        if meta:
            strategies.append(
                Strategy(
                    task_type=str(meta.get("task_type", "optimization")),
                    target_command=str(meta.get("target_command", "")),
                    target_source_dir=str(meta.get("target_source_dir", "")),
                    target_parameter_kind=str(
                        meta.get("target_parameter_kind", "")
                    ),
                    hypothesis_template="",
                    rationale=str(meta.get("strategy_rationale", "")),
                    should_skip_llm=bool(meta.get("should_skip_llm", False)),
                    should_relax_thresholds=bool(
                        meta.get("should_relax_thresholds", False)
                    ),
                    discouraged_targets=tuple(
                        meta.get("discouraged_targets", ())
                    ),
                )
            )
    return strategies


def _read_planning_meta(
    repo_root: Path, cycle_id: str
) -> dict[str, Any] | None:
    """Read _planning_meta from a cycle's assignment JSON."""
    path = (
        repo_root
        / "experiments"
        / cycle_id
        / "agents"
        / "assignments"
        / "candidate_001.json"
    )
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    meta = payload.get("_planning_meta")
    return meta if isinstance(meta, dict) else None


def _format_cycle_id(reference_id: str, number: int) -> str:
    prefix, _sep, ref_num = reference_id.rpartition("_")
    width = len(ref_num)
    return f"{prefix}_{number:0{width}d}"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _increment_cycle_id(cycle_id: str) -> str:
    prefix, sep, number = cycle_id.rpartition("_")
    if not number.isdigit():
        raise ValueError(f"invalid cycle id: {cycle_id}")
    width = len(number)
    return f"{prefix}_{int(number) + 1:0{width}d}"


def _cycle_number(cycle_id: str) -> int:
    _prefix, _sep, number = cycle_id.rpartition("_")
    return int(number)
