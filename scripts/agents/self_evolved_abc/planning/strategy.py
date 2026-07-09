"""Strategy selection for Flow Agent self-evolution.

Given structured cycle evidence, the strategy selector decides:

1. **task_type** — what kind of work should the next cycle do?
2. **target_command** — which evaluation-flow command should be targeted?
3. **target_source_dir** — which source directory implements that command?
4. **target_parameter_kind** — what kind of parameter adjustment?

The core insight: the evaluation flow exercises specific ABC commands. By
tracking which commands produced nonzero signal in prior cycles, we can route
the Flow Agent toward the most promising targets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from scripts.agents.self_evolved_abc.flow.contracts import (
    DEFAULT_EVAL_FLOW_COMMANDS,
    FLOW_SOURCE_TOUCHPOINTS,
)
from scripts.agents.self_evolved_abc.planning.evidence import CycleEvidence


# Subset of eval-flow commands that map to editable source and accept numeric
# parameters.  Others (strash, print_stats) are not useful targets.
_TARGETABLE_COMMANDS: tuple[str, ...] = (
    "fx",
    "rewrite",
    "resub",
    "dc2",
    "csweep",
    "refactor",
)

_PARAMETER_KINDS_BY_COMMAND: dict[str, tuple[str, ...]] = {
    "fx": ("divisor_limit", "weight_threshold", "complement_flag", "lookahead_window"),
    "rewrite": ("cut_size", "iteration_limit", "zero_gain_flag"),
    "resub": ("cut_size", "iteration_limit"),
    "dc2": ("iteration_limit",),
    "csweep": ("cut_limit", "leaf_limit"),
    "refactor": ("cut_size", "iteration_limit", "zero_gain_flag"),
}


@dataclass(frozen=True)
class Strategy:
    """One planning decision for the next Flow Agent cycle."""

    task_type: str
    # optimization | repair | rollback | instrumentation | batch_search | hold

    target_command: str = ""
    # e.g. "fx", "csweep", "rewrite" — which eval-flow command to focus on

    target_source_dir: str = ""
    # e.g. "third_party/FlowTune/src/src/opt/fxu"

    target_parameter_kind: str = ""
    # e.g. "cut_limit", "divisor_limit", "lookahead_window"

    hypothesis_template: str = ""
    # One-sentence hypothesis for the Flow Agent to test

    rationale: str = ""
    # Why this strategy was chosen

    should_skip_llm: bool = False
    # True → recommend batch_search instead of LLM call

    should_relax_thresholds: bool = False
    # True → thresholds are blocking beneficial partial improvements

    discouraged_targets: tuple[str, ...] = ()
    # Source files to avoid in the next cycle


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_strategy(
    evidence: CycleEvidence | None,
    *,
    previous_strategies: Sequence[Strategy] = (),
    cycle_number: int = 1,
    benchmark_count: int = 30,
) -> Strategy:
    """Select the best strategy for the next Flow Agent cycle.

    When ``evidence`` is None (first cycle, no prior data), returns a
    conservative default strategy.
    """
    if evidence is None:
        return _default_strategy(cycle_number)

    # --- Build / CEC failures → repair, no optimization ---
    if evidence.is_build_fail:
        return _repair_strategy(evidence, "build")

    if evidence.is_cec_fail:
        return _repair_strategy(evidence, "cec")

    # --- Champion promoted → exploit the same direction ---
    if evidence.is_champion:
        return _exploit_strategy(evidence, previous_strategies)

    # --- QoR repair (CEC passed but didn't improve enough) ---
    if evidence.is_repair_qor:
        return _qor_repair_strategy(
            evidence, previous_strategies, cycle_number, benchmark_count
        )

    # --- Fallback ---
    return _default_strategy(cycle_number)


# ---------------------------------------------------------------------------
# Strategy builders
# ---------------------------------------------------------------------------


def _default_strategy(cycle_number: int) -> Strategy:
    """First-cycle or fallback: target csweep (most reliable nonzero signal).

    First-cycle planning should still produce an executable LLM task. Batch
    search is reserved for evidence-backed zero-delta or repeated weak-signal
    cycles.
    """
    return Strategy(
        task_type="optimization",
        target_command="csweep",
        target_source_dir="third_party/FlowTune/src/src/opt/csw",
        target_parameter_kind="cut_limit",
        hypothesis_template=(
            "Increase csweep cut/leaf floors unconditionally in Csw_Sweep "
            "before Csw_ManStart to expand the cut-sweeping search space."
        ),
        rationale=(
            "csweep is the only eval-flow command family with confirmed nonzero "
            "signal in batch_search. Targeting known-productive parameters "
            "avoids zero-delta cycles."
        ),
        should_skip_llm=False,
    )


def _repair_strategy(evidence: CycleEvidence, gate: str) -> Strategy:
    return Strategy(
        task_type="repair",
        target_command="",
        target_source_dir="",
        target_parameter_kind="",
        hypothesis_template=(
            f"Repair the {gate} failure from cycle {evidence.cycle_id}. "
            f"Review reason: {evidence.review_reason}"
        ),
        rationale=f"Build/CEC gate failed ({gate}) — must repair before new optimization.",
        should_skip_llm=False,
        discouraged_targets=(
            (evidence.previous_patch_target,)
            if evidence.previous_patch_target
            else ()
        ),
    )


def _exploit_strategy(
    evidence: CycleEvidence,
    previous_strategies: Sequence[Strategy],
) -> Strategy:
    """Champion promoted — exploit the winning direction, vary the parameter."""
    last = previous_strategies[-1] if previous_strategies else None
    improved_benchmarks = [b.benchmark for b in evidence.improved_benchmarks]
    return Strategy(
        task_type="optimization",
        target_command=last.target_command if last else "csweep",
        target_source_dir=last.target_source_dir if last else "",
        target_parameter_kind=last.target_parameter_kind if last else "",
        hypothesis_template=(
            f"Build on the champion from {evidence.cycle_id}. "
            f"Previous patch improved {', '.join(improved_benchmarks[:5])}. "
            "Apply a follow-up change to the same command with a different "
            "parameter to compound gains."
        ),
        rationale=(
            f"Champion in {evidence.cycle_id} proves this direction works. "
            "Exploit before exploring new commands."
        ),
        should_skip_llm=False,
    )


def _qor_repair_strategy(
    evidence: CycleEvidence,
    previous_strategies: Sequence[Strategy],
    cycle_number: int,
    benchmark_count: int,
) -> Strategy:
    """QoR didn't improve enough — decide: switch target, use batch, or relax."""
    discouraged = _collect_discouraged(evidence, previous_strategies)

    # --- Zero delta → reachability problem, switch command ---
    if evidence.all_deltas_zero:
        next_command = _next_untried_command(previous_strategies)
        source_dir = _source_dir_for_command(next_command)
        tried_commands = {s.target_command for s in previous_strategies if s.target_command}
        untried = next_command not in tried_commands
        # Batch-search before LLM when: early cycles, or switching to a
        # never-tried command (batch first to find productive parameter ranges).
        skip_llm = (cycle_number <= 3) or untried
        return Strategy(
            task_type="optimization",
            target_command=next_command,
            target_source_dir=source_dir,
            target_parameter_kind=_default_parameter_kind(next_command),
            hypothesis_template=(
                f"Previous patch on {evidence.previous_patch_target or 'unknown'} "
                "produced ZERO AND delta — the code was never reached by the "
                f"evaluation flow. Target `{next_command}` instead via "
                f"`{source_dir}`. Adjust a numeric parameter (not a debug flag) "
                "that directly controls the command's behavior."
            ),
            rationale=(
                f"Zero delta in {evidence.cycle_id} — switching from unreached "
                f"code to {next_command} (source: {source_dir})."
                + (" Batch-search recommended before LLM." if skip_llm else "")
            ),
            should_skip_llm=skip_llm,
            discouraged_targets=discouraged,
        )

    # --- Small nonzero improvement but failed thresholds → may relax ---
    if evidence.improved_benchmark_count > 0:
        threshold_gap = _diagnose_threshold_gap(evidence, benchmark_count)
        if threshold_gap:
            if _is_repeated_weak_signal(evidence, previous_strategies):
                next_command = _next_untried_command(previous_strategies)
                source_dir = _source_dir_for_command(next_command)
                last_command = _last_target_command(previous_strategies)
                return Strategy(
                    task_type="batch_search",
                    target_command=next_command,
                    target_source_dir=source_dir,
                    target_parameter_kind=_default_parameter_kind(next_command),
                    hypothesis_template=(
                        f"Repeated `{last_command}` patches produced only tiny "
                        f"QoR deltas ({evidence.improved_benchmark_count} changed "
                        f"benchmark(s), total AND reduction "
                        f"{_actual_total_reduction(evidence)}). Do not continue "
                        "single-shot amplification. Run flow_wide batch_search "
                        f"and prioritize `{next_command}` plus command-default "
                        "variants with materially larger expected effect."
                    ),
                    rationale=(
                        f"Repeated weak nonzero QoR on {last_command}: "
                        f"{threshold_gap} Switching/batch-searching before "
                        "another LLM source patch."
                    ),
                    should_skip_llm=True,
                    should_relax_thresholds=False,
                    discouraged_targets=discouraged,
                )
            return Strategy(
                task_type="optimization",
                target_command=_last_target_command(previous_strategies),
                target_source_dir=_last_source_dir(previous_strategies),
                target_parameter_kind=_last_parameter_kind(previous_strategies),
                hypothesis_template=(
                    f"Previous patch showed improvement on "
                    f"{evidence.improved_benchmark_count} benchmark(s) but "
                    f"failed thresholds. {threshold_gap} "
                    "Adjust the same parameter further (or combine with a "
                    "second change) to amplify the effect."
                ),
                rationale=(
                    f"Partial improvement ({evidence.improved_benchmark_count} "
                    f"benchmarks) — amplifying before switching. "
                    f"{threshold_gap}"
                ),
                should_skip_llm=False,
                should_relax_thresholds=(
                    evidence.improved_benchmark_count
                    >= evidence.min_improved_benchmarks - 1
                ),
                discouraged_targets=discouraged,
            )

    # --- Regressions or complete neutral → switch command ---
    next_command = _next_untried_command(previous_strategies)
    return Strategy(
        task_type="optimization",
        target_command=next_command,
        target_source_dir=_source_dir_for_command(next_command),
        target_parameter_kind=_default_parameter_kind(next_command),
        hypothesis_template=(
            f"Previous attempts on all tried commands showed no improvement. "
            f"Try `{next_command}`. Prefer batch_search first to find "
            "productive parameter ranges before another LLM cycle."
        ),
        rationale=(
            f"No improvement from tried commands — rotating to {next_command}. "
            "Recommend batch_search before next LLM call."
        ),
        should_skip_llm=True,
        discouraged_targets=discouraged,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_dir_for_command(command: str) -> str:
    """Map an eval-flow command to its primary source directory."""
    mapping = FLOW_SOURCE_TOUCHPOINTS
    if command == "csweep":
        return "third_party/FlowTune/src/src/opt/csw"
    dirs = mapping.get(command, [])
    return str(dirs[0]) if dirs else "third_party/FlowTune/src/src/opt"


def _default_parameter_kind(command: str) -> str:
    kinds = _PARAMETER_KINDS_BY_COMMAND.get(command, ("numeric_parameter",))
    return kinds[0]


def _next_untried_command(
    previous_strategies: Sequence[Strategy],
) -> str:
    """Pick the next targetable command that hasn't been tried yet."""
    tried = {s.target_command for s in previous_strategies if s.target_command}
    for cmd in _TARGETABLE_COMMANDS:
        if cmd not in tried:
            return cmd
    # All tried — wrap around to csweep (most proven)
    return "csweep"


def _collect_discouraged(
    evidence: CycleEvidence,
    previous_strategies: Sequence[Strategy],
) -> tuple[str, ...]:
    """Build the discouraged-targets list for the next cycle."""
    targets: list[str] = []
    if evidence.previous_patch_target:
        targets.append(evidence.previous_patch_target)
    for s in previous_strategies:
        targets.extend(s.discouraged_targets)
    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return tuple(result)


def _last_target_command(
    previous_strategies: Sequence[Strategy],
) -> str:
    if previous_strategies:
        return previous_strategies[-1].target_command
    return "csweep"


def _last_source_dir(
    previous_strategies: Sequence[Strategy],
) -> str:
    if previous_strategies:
        return previous_strategies[-1].target_source_dir
    return "third_party/FlowTune/src/src/opt/csw"


def _last_parameter_kind(
    previous_strategies: Sequence[Strategy],
) -> str:
    if previous_strategies:
        return previous_strategies[-1].target_parameter_kind
    return "cut_limit"


def _consecutive_target_count(
    previous_strategies: Sequence[Strategy],
    command: str,
) -> int:
    """Count how many trailing strategies used the same target command."""
    if not command:
        return 0
    count = 0
    for strategy in reversed(previous_strategies):
        if strategy.target_command != command:
            break
        count += 1
    return count


def _actual_total_reduction(evidence: CycleEvidence) -> int:
    if evidence.total_and_delta is None:
        return 0
    return max(0, -evidence.total_and_delta)


def _is_repeated_weak_signal(
    evidence: CycleEvidence,
    previous_strategies: Sequence[Strategy],
) -> bool:
    """True when repeated same-command edits are far below promotion signal.

    A tiny nonzero delta is useful evidence, but after repeated attempts it is
    better handled by deterministic batch search or a command switch than by
    another one-off LLM patch.
    """
    last_command = _last_target_command(previous_strategies)
    if _consecutive_target_count(previous_strategies, last_command) < 2:
        return False
    if not evidence.all_cec_pass or evidence.improved_benchmark_count <= 0:
        return False

    avg_required = max(0.0, evidence.min_average_and_improve_pct)
    avg_actual = evidence.average_and_improve_pct
    avg_far_short = (
        avg_required > 0.0
        and avg_actual is not None
        and avg_actual < avg_required * 0.10
    )

    total_required = max(0, evidence.min_total_and_reduction)
    total_far_short = (
        total_required > 0
        and _actual_total_reduction(evidence) < total_required
    )

    breadth_is_minimal = (
        evidence.min_improved_benchmarks > 0
        and evidence.improved_benchmark_count <= evidence.min_improved_benchmarks
    )

    return avg_far_short and (total_far_short or breadth_is_minimal)


def _diagnose_threshold_gap(
    evidence: CycleEvidence,
    benchmark_count: int,
) -> str:
    """Explain why the candidate failed thresholds."""
    gaps: list[str] = []
    if evidence.average_and_improve_pct is not None:
        required = evidence.min_average_and_improve_pct
        if evidence.average_and_improve_pct < required:
            gaps.append(
                f"avg improvement {evidence.average_and_improve_pct:.2f}% "
                f"< required {required:.2f}%"
            )
    if evidence.total_and_delta is not None:
        required_total = evidence.min_total_and_reduction
        actual_reduction = -evidence.total_and_delta
        if actual_reduction < required_total:
            gaps.append(
                f"total reduction {actual_reduction} "
                f"< required {required_total}"
            )
    if evidence.improved_benchmark_count < evidence.min_improved_benchmarks:
        gaps.append(
            f"improved {evidence.improved_benchmark_count}/"
            f"{benchmark_count} < required "
            f"{evidence.min_improved_benchmarks}"
        )
    if not gaps:
        return ""
    return "Gap: " + "; ".join(gaps) + "."
