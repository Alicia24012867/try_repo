"""Deterministic Planning Engine for Flow Agent self-evolution.

Orchestrates: read evidence → select strategy → propose thresholds →
generate next-cycle assignment updates.  No LLM call required — this is a
deterministic rule-based engine that can replace the hard-coded logic in
``next_cycle.py`` when the user chooses to wire it in.

Usage (standalone, not yet wired into the loop)::

    from scripts.agents.self_evolved_abc.planning.engine import PlanningEngine
    from pathlib import Path

    engine = PlanningEngine(Path.cwd())
    result = engine.plan("cycle_001")
    print(result.hypothesis)
    print(result.next_assignment_updates)
"""

from __future__ import annotations

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

    Reads completed cycle evidence, selects a strategy, proposes adaptive
    thresholds, and generates a concrete planner hypothesis that can be
    injected directly into the Flow Agent's ``planner_hypothesis`` field.
    """

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()
        self._history: list[CycleEvidence] = []
        self._strategies: list[Strategy] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, previous_cycle_id: str) -> PlanningResult | None:
        """Plan the next cycle based on the previous cycle's evidence.

        Returns None when the previous cycle hasn't been evaluated yet.
        """
        evidence = read_cycle_evidence(
            self._repo_root, previous_cycle_id
        )
        if evidence is not None:
            self._history.append(evidence)

        # Determine which cycle we're planning FOR
        next_cycle_id = _increment_cycle_id(previous_cycle_id)
        cycle_number = _cycle_number(next_cycle_id)

        # Count benchmarks from the evidence or a reasonable default
        benchmark_count = (
            len(evidence.per_benchmark)
            if evidence is not None and evidence.per_benchmark
            else 30
        )

        # --- Thresholds ---
        thresholds = propose_thresholds(
            benchmark_count=benchmark_count,
            previous_evidence=tuple(self._history),
            cycle_number=cycle_number,
        )

        # --- Strategy ---
        strategy = select_strategy(
            evidence,
            previous_strategies=tuple(self._strategies),
            cycle_number=cycle_number,
            benchmark_count=benchmark_count,
        )
        self._strategies.append(strategy)

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
            history=list(self._history),
            previous_strategies=list(self._strategies),
        )

    def plan_multi(
        self, start_cycle: str, end_cycle: str
    ) -> list[PlanningResult]:
        """Plan across a range of already-completed cycles.

        Reads each cycle's evidence in order, accumulating history so later
        plans can learn from earlier outcomes.
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
        """Build the fields to merge into a next-cycle assignment.

        These can be passed to ``next_cycle.py`` or used to hand-author
        an assignment JSON.
        """
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
