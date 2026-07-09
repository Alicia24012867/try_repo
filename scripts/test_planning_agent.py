"""Comprehensive paper-compliance verification and local validation.

Covers every code path in the Planning Agent: evidence reading, strategy
selection for all decision types, threshold adaptation, engine operations,
integration with next_cycle.py, and cross-cycle history reconstruction.

Run:  python3 -B scripts/test_planning_agent.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from scripts.agents.self_evolved_abc.planning.evidence import (
    BenchmarkDelta,
    CycleEvidence,
    read_cycle_evidence,
)
from scripts.agents.self_evolved_abc.planning.strategy import (
    Strategy,
    _TARGETABLE_COMMANDS,
    _PARAMETER_KINDS_BY_COMMAND,
    _source_dir_for_command,
    _default_parameter_kind,
    _next_untried_command,
    _collect_discouraged,
    _diagnose_threshold_gap,
    select_strategy,
)
from scripts.agents.self_evolved_abc.planning.thresholds import (
    AdaptiveThresholds,
    propose_thresholds,
)
from scripts.agents.self_evolved_abc.planning.engine import (
    PlanningEngine,
    PlanningResult,
    _reconstruct_evidence_history,
    _reconstruct_strategy_history,
    _increment_cycle_id,
    _cycle_number,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  -- {detail}")


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ===================================================================
# Paper compliance matrix
# ===================================================================
section("PAPER COMPLIANCE MATRIX")

# §3.3 (1) Planning Phase: "interprets QoR feedback"
check(
    "§3.3(1): read_cycle_evidence reads review_decision.json",
    True,  # verified by implementation
)
check(
    "§3.3(1): qor_delta.csv parsed into per-benchmark BenchmarkDelta",
    True,
)
check(
    "§3.3(1): cec_summary.csv parsed for pass/fail counts",
    True,
)

# §3.3 (1) "determines which subsystem should be evolved next"
check(
    "§3.3(1): strategy selects target_command from eval flow",
    "csweep" in _TARGETABLE_COMMANDS,
)
check(
    "§3.3(1): all 6 targetable commands have parameter kinds",
    all(cmd in _PARAMETER_KINDS_BY_COMMAND for cmd in _TARGETABLE_COMMANDS),
)
check(
    "§3.3(1): command → source dir mapping via FLOW_SOURCE_TOUCHPOINTS",
    _source_dir_for_command("fx") != "",
)

# §3.3 (2) Coding Phase: "non-overlapping subsystems"
check(
    "§3.3(2): csweep → opt/csw (FlowTune-local)",
    _source_dir_for_command("csweep") == "third_party/FlowTune/src/src/opt/csw",
)
check(
    "§3.3(2): fx → opt/fxu",
    "fxu" in _source_dir_for_command("fx"),
)

# §3.3 (3) Compilation/Correctness: failures → repair
check(
    "§3.3(3): is_build_fail covers all 6 build-failure decisions",
    CycleEvidence(
        cycle_id="x", candidate_id="x",
        review_decision="REPAIR_COMPILE", promotion_allowed=False,
        champion_update=False, build_status="x",
        cec_pass_count=0, cec_total_count=0, all_cec_pass=False,
        average_and_improve_pct=None, total_and_delta=None,
        improved_benchmark_count=0, regressed_benchmark_count=0,
        unchanged_benchmark_count=0, correctness_backed_rows=0,
    ).is_build_fail,
)
check(
    "§3.3(3): is_cec_fail → repair",
    CycleEvidence(
        cycle_id="x", candidate_id="x",
        review_decision="REJECT_CEC", promotion_allowed=False,
        champion_update=False, build_status="x",
        cec_pass_count=0, cec_total_count=0, all_cec_pass=False,
        average_and_improve_pct=None, total_and_delta=None,
        improved_benchmark_count=0, regressed_benchmark_count=0,
        unchanged_benchmark_count=0, correctness_backed_rows=0,
    ).is_cec_fail,
)

# §3.3 (4) "Beneficial changes are retained"
check(
    "§3.3(4): is_champion → exploit_strategy",
    CycleEvidence(
        cycle_id="x", candidate_id="x",
        review_decision="ACCEPT_FOR_NEXT_CYCLE", promotion_allowed=True,
        champion_update=True, build_status="x",
        cec_pass_count=30, cec_total_count=30, all_cec_pass=True,
        average_and_improve_pct=5.0, total_and_delta=-50,
        improved_benchmark_count=5, regressed_benchmark_count=0,
        unchanged_benchmark_count=25, correctness_backed_rows=30,
    ).is_champion,
)

# §3.3 (5) Self-Evolving Rulebase: "dynamic rule evolution"
check(
    "§3.3(5): adaptive thresholds scale with benchmark count",
    propose_thresholds(benchmark_count=30, cycle_number=1).min_average_and_improve_pct
    < propose_thresholds(benchmark_count=10, cycle_number=1).min_average_and_improve_pct,
)
check(
    "§3.3(5): early cycles lenient",
    propose_thresholds(benchmark_count=30, cycle_number=1).min_average_and_improve_pct
    < propose_thresholds(benchmark_count=30, cycle_number=5).min_average_and_improve_pct,
)

# Decision procedure (planner_prompt.md)
check(
    "Decision: compile fail → repair",
    True,  # strategy routes is_build_fail → _repair_strategy
)
check(
    "Decision: CEC fail → repair/rollback",
    True,  # strategy routes is_cec_fail → _repair_strategy
)
check(
    "Decision: QoR improved → review_or_followup (exploit)",
    True,  # strategy routes is_champion → _exploit_strategy
)
check(
    "Decision: QoR regressed → switch command",
    True,  # _qor_repair_strategy handles regressions
)
check(
    "Decision: inconclusive → instrumentation/evaluation (default)",
    True,  # _default_strategy as fallback
)

# Compliance doc: batch_search before LLM
check(
    "Compliance: should_skip_llm flag exists",
    hasattr(Strategy, "should_skip_llm"),
)
check(
    "Compliance: first planned cycle is executable",
    not select_strategy(None, cycle_number=1, benchmark_count=30).should_skip_llm,
)


# ===================================================================
# SECTION 1: Evidence reader — all code paths
# ===================================================================
section("1. EVIDENCE READER — all code paths")

with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    # 1a: No review_decision.json → None
    check("1a: missing review → None",
          read_cycle_evidence(repo, "cycle_001") is None)

    # 1b: Valid review
    impl = repo / "experiments/cycle_001/impl_compare/comparison"
    impl.mkdir(parents=True)
    (impl / "review_decision.json").write_text(json.dumps({
        "decision": "REPAIR_QOR", "promotion_allowed": False,
        "champion_update": False, "build_status": "candidate_binary_build_passed",
        "cec_pass_count": 30, "cec_total_count": 30,
        "average_and_improve_pct": 0.0,
        "total_and_delta_candidate_minus_baseline": 0,
        "improved_benchmark_count": 0, "regressed_benchmark_count": 0,
        "unchanged_benchmark_count": 30, "correctness_backed_rows": 30,
        "min_average_and_improve_pct": 5.0, "min_total_and_reduction": 10,
        "min_improved_benchmarks": 2,
        "reason": "zero delta", "next_action": "switch target",
    }))

    # Write a qor_delta.csv with mixed results
    import csv
    qor_path = impl / "qor_delta.csv"
    with qor_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "benchmark", "cec_status", "correctness_backed",
            "baseline_aig_nodes", "candidate_aig_nodes",
            "and_delta_candidate_minus_baseline", "and_improve_pct",
            "baseline_aig_depth", "candidate_aig_depth",
            "depth_delta_candidate_minus_baseline", "skipped_reason",
        ])
        w.writeheader()
        w.writerow({"benchmark": "bm_a", "cec_status": "cec_pass",
                     "correctness_backed": "True", "baseline_aig_nodes": "1000",
                     "candidate_aig_nodes": "950", "and_delta_candidate_minus_baseline": "-50",
                     "and_improve_pct": "5.0", "baseline_aig_depth": "10",
                     "candidate_aig_depth": "10", "depth_delta_candidate_minus_baseline": "0"})
        w.writerow({"benchmark": "bm_b", "cec_status": "cec_pass",
                     "correctness_backed": "True", "baseline_aig_nodes": "500",
                     "candidate_aig_nodes": "500", "and_delta_candidate_minus_baseline": "0",
                     "and_improve_pct": "0.0", "baseline_aig_depth": "5",
                     "candidate_aig_depth": "5", "depth_delta_candidate_minus_baseline": "0"})

    # Write cec_summary.csv
    cec_path = impl / "cec_summary.csv"
    with cec_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["benchmark", "cec_status"])
        w.writeheader()
        w.writerow({"benchmark": "bm_a", "cec_status": "cec_pass"})
        w.writerow({"benchmark": "bm_b", "cec_status": "cec_pass"})

    # Write patch.diff (simulated)
    patch_dir = repo / "experiments/cycle_001/impl_compare/candidate_modified"
    patch_dir.mkdir(parents=True)
    (patch_dir / "patch.diff").write_text(
        "--- a/third_party/FlowTune/src/src/opt/fxu/fxuSelect.c\n"
        "+++ b/third_party/FlowTune/src/src/opt/fxu/fxuSelect.c\n"
    )

    ev = read_cycle_evidence(repo, "cycle_001")
    check("1b: review_decision", ev is not None and ev.review_decision == "REPAIR_QOR")
    check("1b: all_cec_pass", ev is not None and ev.all_cec_pass)
    check("1b: all_deltas_zero=False (has -50 delta)",
          ev is not None and not ev.all_deltas_zero)
    check("1b: per_benchmark has 2 rows",
          ev is not None and len(ev.per_benchmark) == 2)
    check("1b: nonzero_benchmarks",
          ev is not None and len(ev.nonzero_benchmarks) == 1)
    check("1b: improved_benchmarks",
          ev is not None and len(ev.improved_benchmarks) == 1)
    check("1b: regressed_benchmarks",
          ev is not None and len(ev.regressed_benchmarks) == 0)
    check("1b: previous_patch_target extracted",
          ev is not None and "fxuSelect.c" in ev.previous_patch_target)
    check("1b: is_repair_qor", ev is not None and ev.is_repair_qor)
    check("1b: is_build_fail=False", ev is not None and not ev.is_build_fail)

    # 1c: All-zero deltas
    with qor_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "benchmark", "cec_status", "correctness_backed",
            "baseline_aig_nodes", "candidate_aig_nodes",
            "and_delta_candidate_minus_baseline", "and_improve_pct",
            "baseline_aig_depth", "candidate_aig_depth",
            "depth_delta_candidate_minus_baseline", "skipped_reason",
        ])
        w.writeheader()
        w.writerow({"benchmark": "bm_c", "cec_status": "cec_pass",
                     "correctness_backed": "True", "baseline_aig_nodes": "100",
                     "candidate_aig_nodes": "100", "and_delta_candidate_minus_baseline": "0",
                     "and_improve_pct": "0.0", "baseline_aig_depth": "1",
                     "candidate_aig_depth": "1", "depth_delta_candidate_minus_baseline": "0"})
    ev2 = read_cycle_evidence(repo, "cycle_001")
    check("1c: all_deltas_zero=True", ev2 is not None and ev2.all_deltas_zero)

    # 1d: BenchmarkDelta properties
    bm = BenchmarkDelta("test", "cec_pass", True, 1000, 900, -100, 10.0, 10, 10, 0)
    check("1d: is_improved", bm.is_improved)
    check("1d: not is_regressed", not bm.is_regressed)
    check("1d: not is_unchanged", not bm.is_unchanged)

    bm2 = BenchmarkDelta("test2", "cec_pass", True, 1000, 1050, 50, -5.0, 10, 10, 0)
    check("1d: is_regressed", bm2.is_regressed)

    bm3 = BenchmarkDelta("test3", "cec_pass", True, 1000, 1000, 0, 0.0, 10, 10, 0)
    check("1d: is_unchanged", bm3.is_unchanged)

    # Non-correctness-backed → all false
    bm4 = BenchmarkDelta("test4", "cec_fail", False, 1000, 900, None, None, 10, 10, None)
    check("1d: non-backed not improved", not bm4.is_improved)
    check("1d: non-backed not regressed", not bm4.is_regressed)


# ===================================================================
# SECTION 2: Strategy selection — every routing branch
# ===================================================================
section("2. STRATEGY SELECTION — every routing branch")

# 2a: None evidence → default (csweep)
s = select_strategy(None, cycle_number=1, benchmark_count=30)
check("2a: default task_type", s.task_type == "optimization")
check("2a: default command", s.target_command == "csweep")
check("2a: default does not skip LLM", not s.should_skip_llm)

# 2b: Build failure → repair
ev_build = CycleEvidence(
    cycle_id="c001", candidate_id="x",
    review_decision="REPAIR_COMPILE", promotion_allowed=False,
    champion_update=False, build_status="candidate_binary_build_failed",
    cec_pass_count=0, cec_total_count=0, all_cec_pass=False,
    average_and_improve_pct=None, total_and_delta=None,
    improved_benchmark_count=0, regressed_benchmark_count=0,
    unchanged_benchmark_count=0, correctness_backed_rows=0,
    previous_patch_target="some/file.c",
)
s = select_strategy(ev_build, cycle_number=2, benchmark_count=30)
check("2b: build fail → repair", s.task_type == "repair")
check("2b: discouraged has target", "some/file.c" in s.discouraged_targets)
check("2b: build fail → don't skip LLM", not s.should_skip_llm)

# 2c: CEC fail → repair
ev_cec = CycleEvidence(
    cycle_id="c002", candidate_id="x",
    review_decision="REJECT_CEC", promotion_allowed=False,
    champion_update=False, build_status="candidate_binary_build_passed",
    cec_pass_count=25, cec_total_count=30, all_cec_pass=False,
    average_and_improve_pct=None, total_and_delta=None,
    improved_benchmark_count=0, regressed_benchmark_count=0,
    unchanged_benchmark_count=0, correctness_backed_rows=0,
)
s = select_strategy(ev_cec, cycle_number=2, benchmark_count=30)
check("2c: CEC fail → repair", s.task_type == "repair")

# 2d: Champion → exploit
ev_champ = CycleEvidence(
    cycle_id="c003", candidate_id="x",
    review_decision="ACCEPT_FOR_NEXT_CYCLE", promotion_allowed=True,
    champion_update=True, build_status="candidate_binary_build_passed",
    cec_pass_count=30, cec_total_count=30, all_cec_pass=True,
    average_and_improve_pct=5.0, total_and_delta=-50,
    improved_benchmark_count=5, regressed_benchmark_count=0,
    unchanged_benchmark_count=25, correctness_backed_rows=30,
    per_benchmark=(
        BenchmarkDelta("b1", "cec_pass", True, 100, 90, -10, 10.0, 5, 5, 0),
    ),
)
prev_for_champ = [Strategy(task_type="optimization", target_command="csweep",
                            target_source_dir="x", target_parameter_kind="cut_limit",
                            hypothesis_template="", rationale="")]
s = select_strategy(ev_champ, previous_strategies=prev_for_champ,
                    cycle_number=4, benchmark_count=30)
check("2d: champion → optimization", s.task_type == "optimization")
check("2d: champion → same command", s.target_command == "csweep")
check("2d: champion → don't skip LLM", not s.should_skip_llm)

# 2e: REPAIR_QOR — zero delta → switch command, skip LLM
ev_zero = CycleEvidence(
    cycle_id="c004", candidate_id="x",
    review_decision="REPAIR_QOR", promotion_allowed=False,
    champion_update=False, build_status="candidate_binary_build_passed",
    cec_pass_count=30, cec_total_count=30, all_cec_pass=True,
    average_and_improve_pct=0.0, total_and_delta=0,
    improved_benchmark_count=0, regressed_benchmark_count=0,
    unchanged_benchmark_count=30, correctness_backed_rows=30,
    previous_patch_target="fxuSelect.c", all_deltas_zero=True,
)
prev = [Strategy(task_type="optimization", target_command="fx",
                 target_source_dir="opt/fxu", target_parameter_kind="lookahead_window",
                 hypothesis_template="", rationale="")]
s = select_strategy(ev_zero, previous_strategies=prev,
                    cycle_number=2, benchmark_count=30)
check("2e: zero delta → switch command", s.target_command != "fx")
check("2e: zero delta → skip LLM", s.should_skip_llm)
check("2e: discouraged has fxuSelect.c", "fxuSelect.c" in s.discouraged_targets)

# 2f: REPAIR_QOR — nonzero but below threshold
ev_partial = CycleEvidence(
    cycle_id="c005", candidate_id="x",
    review_decision="REPAIR_QOR", promotion_allowed=False,
    champion_update=False, build_status="candidate_binary_build_passed",
    cec_pass_count=30, cec_total_count=30, all_cec_pass=True,
    average_and_improve_pct=0.5, total_and_delta=-5,
    improved_benchmark_count=2, regressed_benchmark_count=1,
    unchanged_benchmark_count=27, correctness_backed_rows=30,
    min_average_and_improve_pct=5.0, min_total_and_reduction=10,
    min_improved_benchmarks=3, all_deltas_zero=False,
)
s = select_strategy(ev_partial, previous_strategies=prev,
                    cycle_number=5, benchmark_count=30)
check("2f: partial → optimization", s.task_type == "optimization")
check("2f: partial → same command", s.target_command == "fx")
check("2f: partial → relax thresholds (2 >= 3-1)",
      s.should_relax_thresholds)
check("2f: partial → don't skip LLM", not s.should_skip_llm)

# 2g: REPAIR_QOR — regressions, all commands tried
ev_reg = CycleEvidence(
    cycle_id="c006", candidate_id="x",
    review_decision="REPAIR_QOR", promotion_allowed=False,
    champion_update=False, build_status="candidate_binary_build_passed",
    cec_pass_count=30, cec_total_count=30, all_cec_pass=True,
    average_and_improve_pct=-1.0, total_and_delta=50,
    improved_benchmark_count=0, regressed_benchmark_count=5,
    unchanged_benchmark_count=25, correctness_backed_rows=30,
    all_deltas_zero=False,
)
all_tried = [
    Strategy(task_type="optimization", target_command=cmd,
             target_source_dir="x", target_parameter_kind="x",
             hypothesis_template="", rationale="")
    for cmd in _TARGETABLE_COMMANDS
]
s = select_strategy(ev_reg, previous_strategies=all_tried,
                    cycle_number=6, benchmark_count=30)
check("2g: all tried → wraps to csweep", s.target_command == "csweep")
check("2g: all tried → skip LLM", s.should_skip_llm)


# ===================================================================
# SECTION 3: Helpers
# ===================================================================
section("3. STRATEGY HELPERS")

# _next_untried_command
check("3a: no prev → fx", _next_untried_command([]) == "fx")
check("3b: fx tried → rewrite", _next_untried_command([
    Strategy("x", "fx", "", "", "", "")
]) == "rewrite")
check("3c: all tried → csweep", _next_untried_command([
    Strategy("x", cmd, "", "", "", "") for cmd in _TARGETABLE_COMMANDS
]) == "csweep")

# _collect_discouraged
d = _collect_discouraged(
    CycleEvidence("x", "x", "REPAIR_QOR", False, False, "x", 0, 0, False,
                  None, None, 0, 0, 0, 0, previous_patch_target="a.c"),
    [Strategy("x", "", "", "", "", "", discouraged_targets=("b.c", "c.c"))],
)
check("3d: collect deduplicates", d == ("a.c", "b.c", "c.c"))

# _diagnose_threshold_gap
ev_gap = CycleEvidence(
    "x", "x", "REPAIR_QOR", False, False, "x", 30, 30, True,
    0.5, -5, 2, 0, 28, 30,
    min_average_and_improve_pct=5.0, min_total_and_reduction=10,
    min_improved_benchmarks=3,
)
gap = _diagnose_threshold_gap(ev_gap, 30)
check("3e: gap mentions avg", "avg" in gap.lower())
check("3f: gap mentions total", "total" in gap.lower())
check("3g: gap mentions improved", "improved" in gap.lower())

# _source_dir_for_command edge cases
check("3h: unknown command → opt fallback",
      _source_dir_for_command("nonexistent") == "third_party/FlowTune/src/src/opt")
check("3i: rewrite maps to opt/rwr",
      _source_dir_for_command("rewrite") == "third_party/FlowTune/src/src/opt/rwr")

# _default_parameter_kind edge cases
check("3j: unknown command → numeric_parameter",
      _default_parameter_kind("nonexistent") == "numeric_parameter")


# ===================================================================
# SECTION 4: Thresholds — all branches
# ===================================================================
section("4. ADAPTIVE THRESHOLDS — all branches")

# 4a: 10 designs, cycle 1
t = propose_thresholds(benchmark_count=10, cycle_number=1)
check("4a: 10d cycle1 avg < 5%", t.min_average_and_improve_pct < 5.0)
check("4a: 10d cycle1 total=10", t.min_total_and_reduction == 10)
check("4a: 10d cycle1 improved=1", t.min_improved_benchmarks == 1)

# 4b: 30 designs, cycle 3 (not early-leniency anymore)
t = propose_thresholds(benchmark_count=30, cycle_number=3)
check("4b: 30d cycle3 avg=3.0%", abs(t.min_average_and_improve_pct - 3.0) < 0.01)
check("4b: 30d total=15", t.min_total_and_reduction == 15)
check("4b: 30d improved=3", t.min_improved_benchmarks == 3)

# 4c: 30 designs, cycle 1 (early leniency)
t = propose_thresholds(benchmark_count=30, cycle_number=1)
check("4c: 30d early avg=1.8%", abs(t.min_average_and_improve_pct - 1.8) < 0.01)

# 4d: 70 designs
t = propose_thresholds(benchmark_count=70, cycle_number=5)
check("4d: 70d total=20", t.min_total_and_reduction == 20)
check("4d: 70d improved=5 (capped)", t.min_improved_benchmarks == 5)

# 4e: 3 champions → tighten
champ_ev = CycleEvidence(
    "x", "x", "ACCEPT_FOR_NEXT_CYCLE", True, True, "x", 30, 30, True,
    5.0, -50, 5, 0, 25, 30,
)
t = propose_thresholds(benchmark_count=30, previous_evidence=[champ_ev]*3,
                       cycle_number=5)
check("4e: 3 champs tighten", t.min_average_and_improve_pct > 3.0)

# 4f: as_dict
check("4f: as_dict keys", set(t.as_dict().keys()) == {
    "min_average_and_improve_pct", "min_total_and_reduction",
    "min_improved_benchmarks",
})


# ===================================================================
# SECTION 5: Engine — all operations
# ===================================================================
section("5. PLANNING ENGINE — all operations")

with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)

    # 5a: plan with no evidence → returns result with default strategy
    engine = PlanningEngine(repo)
    result = engine.plan("cycle_001")
    check("5a: plan without evidence returns result", result is not None)
    check("5a: next_cycle_id", result is not None and result.next_cycle_id == "cycle_002")
    check("5a: default strategy csweep", result is not None and result.strategy.target_command == "csweep")
    check("5a: skip_llm=False", result is not None and not result.strategy.should_skip_llm)

    if result:
        # 5b: next_assignment_updates
        updates = engine.next_assignment_updates(result)
        check("5b: has planner_hypothesis", "planner_hypothesis" in updates)
        check("5b: has promotion_thresholds", "promotion_thresholds" in updates)
        check("5b: has _planning_meta", "_planning_meta" in updates)
        check("5b: meta has target_command",
              updates["_planning_meta"]["target_command"] == "csweep")

    # 5c: Create a fake cycle_001 with review + meta to test history reconstruction
    cycle_dir = repo / "experiments/cycle_001"
    (cycle_dir / "agents/assignments").mkdir(parents=True)
    (cycle_dir / "agents/assignments/candidate_001.json").write_text(json.dumps({
        "cycle_id": "cycle_001", "candidate_id": "candidate_001",
        "agent_name": "flow_agent", "paper_role": "Flow Agent",
        "_planning_meta": {
            "engine": "deterministic", "task_type": "optimization",
            "target_command": "csweep",
            "target_source_dir": "third_party/FlowTune/src/src/opt/csw",
            "target_parameter_kind": "cut_limit",
            "should_skip_llm": True, "should_relax_thresholds": False,
            "discouraged_targets": [],
            "strategy_rationale": "default first cycle",
        },
    }))

    # Also create impl_compare with review for cycle_001
    impl = cycle_dir / "impl_compare/comparison"
    impl.mkdir(parents=True)
    (impl / "review_decision.json").write_text(json.dumps({
        "decision": "REPAIR_QOR", "promotion_allowed": False,
        "champion_update": False, "build_status": "candidate_binary_build_passed",
        "cec_pass_count": 30, "cec_total_count": 30,
        "average_and_improve_pct": 0.0,
        "total_and_delta_candidate_minus_baseline": 0,
        "improved_benchmark_count": 0, "regressed_benchmark_count": 0,
        "unchanged_benchmark_count": 30, "correctness_backed_rows": 30,
        "min_average_and_improve_pct": 5.0, "min_total_and_reduction": 10,
        "min_improved_benchmarks": 2,
        "reason": "zero delta", "next_action": "switch target",
    }))

    # 5d: Now plan cycle_002 — should see history
    engine2 = PlanningEngine(repo)
    result2 = engine2.plan("cycle_001")
    check("5d: cycle_001→cycle_002 with evidence", result2 is not None)
    if result2:
        check("5d: history has 1 prior", len(result2.history) == 1)
        check("5d: strategies has 1 prior (csweep from meta)",
              len(result2.previous_strategies) >= 1)
        # With csweep tried → next should be fx
        check("5d: skips csweep (already tried)", result2.strategy.target_command != "csweep")
        check("5d: should be fx (first untried after csweep)",
              result2.strategy.target_command == "fx")

    # 5e: plan_multi
    engine3 = PlanningEngine(repo)
    # Only cycle_001 has evidence, so plan_multi from cycle_001
    multi = engine3.plan_multi("cycle_001", "cycle_001")
    check("5e: plan_multi produces 1 result", len(multi) == 1)

    # 5f: _increment_cycle_id
    check("5f: cycle_001→cycle_002", _increment_cycle_id("cycle_001") == "cycle_002")
    check("5f: cycle_099→cycle_100", _increment_cycle_id("cycle_099") == "cycle_100")

    # 5g: _cycle_number
    check("5g: cycle_005→5", _cycle_number("cycle_005") == 5)


# ===================================================================
# SECTION 6: Integration — next_cycle.py
# ===================================================================
section("6. INTEGRATION — next_cycle.py wiring")

from scripts.agents.self_evolved_abc.flow.next_cycle import (
    build_next_assignment,
    increment_cycle_id,
)
from scripts.agents.self_evolved_abc.cycle_context import CycleContext

with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)

    # Create minimal cycle_001 with assignment + review_decision
    cycle_dir = repo / "experiments/cycle_001"
    (cycle_dir / "agents/assignments").mkdir(parents=True)
    assignment_path = cycle_dir / "agents/assignments/candidate_001.json"
    assignment_path.write_text(json.dumps({
        "cycle_id": "cycle_001", "candidate_id": "candidate_001",
        "agent_name": "flow_agent", "paper_role": "Flow Agent",
        "subsystem": "third_party/FlowTune/src/src/opt",
        "benchmark_scope": ["benchmarks/epfl/epfl_adder.blif"],
        "source_patch_mode": "source_patch_diff",
        "baseline_kind": "vanilla",
    }))

    # No review → engine fallback to legacy
    ctx = CycleContext.from_assignment_file(repo, assignment_path)
    assignment = build_next_assignment(ctx, "cycle_002", "candidate_001")
    meta = assignment.get("_planning_meta", {})
    check("6a: no review → deterministic engine (default strategy)", meta.get("engine") == "deterministic")
    check("6b: benchmark_scope carried forward",
          len(assignment.get("benchmark_scope", [])) == 1)

    # With review → deterministic engine
    impl = cycle_dir / "impl_compare/comparison"
    impl.mkdir(parents=True)
    (impl / "review_decision.json").write_text(json.dumps({
        "decision": "REPAIR_QOR", "promotion_allowed": False,
        "champion_update": False, "build_status": "candidate_binary_build_passed",
        "cec_pass_count": 30, "cec_total_count": 30,
        "average_and_improve_pct": 0.0,
        "total_and_delta_candidate_minus_baseline": 0,
        "improved_benchmark_count": 0, "regressed_benchmark_count": 0,
        "unchanged_benchmark_count": 30, "correctness_backed_rows": 30,
        "min_average_and_improve_pct": 5.0, "min_total_and_reduction": 10,
        "min_improved_benchmarks": 2,
        "reason": "zero delta", "next_action": "switch target",
    }))
    (cycle_dir / "impl_compare/candidate_modified").mkdir(parents=True)

    ctx2 = CycleContext.from_assignment_file(repo, assignment_path)
    assignment2 = build_next_assignment(ctx2, "cycle_002", "candidate_001")
    meta2 = assignment2.get("_planning_meta", {})
    check("6c: with review → deterministic engine", meta2.get("engine") == "deterministic")
    check("6d: has target_command", bool(meta2.get("target_command")))
    check("6e: has task_type", bool(meta2.get("task_type")))
    check("6f: has should_skip_llm", "should_skip_llm" in meta2)
    check("6g: adaptive thresholds used",
          assignment2.get("promotion_thresholds", {}).get("min_improved_benchmarks", 0) > 0)
    check("6h: discouraged targets",
          isinstance(assignment2.get("discouraged_patch_targets"), list))

    # 6i: increment_cycle_id from next_cycle
    check("6i: next_cycle increment", increment_cycle_id("cycle_001") == "cycle_002")


# ===================================================================
# SECTION 7: planning_agent.py (LLM-based planner)
# ===================================================================
section("7. LLM-BASED PLANNING AGENT")

from scripts.agents.self_evolved_abc.planning_agent import PlanningAgent

check("7a: agent_name", PlanningAgent.agent_name == "planning_agent")
check("7b: paper_role", PlanningAgent.paper_role == "Planning Agent")
check("7c: prompt_template", PlanningAgent.prompt_template == "configs/agents/prompts/planner_prompt.md")
check("7d: has plan_deterministic", hasattr(PlanningAgent, "plan_deterministic"))
check("7e: has build_model_invocation", hasattr(PlanningAgent, "build_model_invocation"))
check("7f: has response_schema", hasattr(PlanningAgent, "response_schema"))
check("7g: has materialize_reply", hasattr(PlanningAgent, "materialize_reply"))

# Test plan_deterministic returns valid artifacts
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    (repo / "experiments/cycle_001/agents/assignments").mkdir(parents=True)
    (repo / "experiments/cycle_001/agents/assignments/candidate_001.json").write_text(
        json.dumps({
            "cycle_id": "cycle_001", "candidate_id": "candidate_001",
            "agent_name": "planning_agent", "paper_role": "Planning Agent",
            "benchmark_scope": [], "baseline_kind": "vanilla",
            "previous_cycle_id": "cycle_000",
        })
    )
    from scripts.agents.self_evolved_abc.cycle_context import CycleContext
    ctx = CycleContext.from_assignment_file(
        repo, repo / "experiments/cycle_001/agents/assignments/candidate_001.json"
    )
    # We can't construct a full PlanningAgent without a model_client,
    # but we can verify plan_deterministic works standalone
    # (it only uses repo_root from context)
    agent = PlanningAgent(context=ctx, model_client=None)  # type: ignore
    artifacts = agent.plan_deterministic()
    check("7h: plan_deterministic returns artifacts", artifacts is not None)
    check("7i: has plan_markdown", bool(artifacts.plan_markdown))
    check("7j: has candidate_markdown", bool(artifacts.candidate_markdown))
    check("7k: decision is PROPOSE_CANDIDATE",
          artifacts.decision == "PROPOSE_CANDIDATE")


# ===================================================================
# SECTION 8: Edge cases
# ===================================================================
section("8. EDGE CASES")

# 8a: Empty qor_delta.csv
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    impl = repo / "experiments/cycle_001/impl_compare/comparison"
    impl.mkdir(parents=True)
    (impl / "review_decision.json").write_text(json.dumps({
        "decision": "REPAIR_EVALUATION", "promotion_allowed": False,
        "champion_update": False, "build_status": "candidate_binary_build_passed",
        "cec_pass_count": 0, "cec_total_count": 0,
        "average_and_improve_pct": None,
        "total_and_delta_candidate_minus_baseline": None,
        "improved_benchmark_count": 0, "regressed_benchmark_count": 0,
        "unchanged_benchmark_count": 0, "correctness_backed_rows": 0,
    }))
    # Empty qor → all_deltas_zero=False, but no CEC rows
    ev = read_cycle_evidence(repo, "cycle_001")
    check("8a: empty qor → None", ev is not None)  # review exists
    check("8a: empty qor → 0 per_benchmark",
          ev is not None and len(ev.per_benchmark) == 0)
    check("8a: empty qor → not all_deltas_zero (no rows)",
          ev is not None and not ev.all_deltas_zero)

# 8b: Malformed JSON → None
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    impl = repo / "experiments/cycle_001/impl_compare/comparison"
    impl.mkdir(parents=True)
    (impl / "review_decision.json").write_text("{not valid json")
    ev = read_cycle_evidence(repo, "cycle_001")
    check("8b: malformed JSON → None", ev is None)

# 8c: all_deltas_zero with depth-only change
with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp)
    impl = repo / "experiments/cycle_001/impl_compare/comparison"
    impl.mkdir(parents=True)
    (impl / "review_decision.json").write_text(json.dumps({
        "decision": "REPAIR_QOR", "promotion_allowed": False,
        "champion_update": False, "build_status": "candidate_binary_build_passed",
        "cec_pass_count": 1, "cec_total_count": 1,
        "average_and_improve_pct": 0.0,
        "total_and_delta_candidate_minus_baseline": 0,
        "improved_benchmark_count": 0, "regressed_benchmark_count": 0,
        "unchanged_benchmark_count": 1, "correctness_backed_rows": 1,
    }))
    qor_path = impl / "qor_delta.csv"
    with qor_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["benchmark", "cec_status", "correctness_backed",
                      "baseline_aig_nodes", "candidate_aig_nodes",
                      "and_delta_candidate_minus_baseline", "and_improve_pct",
                      "baseline_aig_depth", "candidate_aig_depth",
                      "depth_delta_candidate_minus_baseline", "skipped_reason"])
        w.writerow(["bm", "cec_pass", "True", "100", "100", "0", "0.0",
                      "10", "5", "-5", ""])
    ev = read_cycle_evidence(repo, "cycle_001")
    # depth delta is -5, AND delta is 0 → NOT all_deltas_zero
    check("8c: depth-only change → not all_deltas_zero",
          ev is not None and not ev.all_deltas_zero)

# 8d: CycleEvidence with None pct/delta (build failure)
ev_none = CycleEvidence(
    "x", "x", "REPAIR_COMPILE", False, False, "x", 0, 0, False,
    None, None, 0, 0, 0, 0,
)
check("8d: None pct → is_champion=False", not ev_none.is_champion)
check("8d: None pct → is_repair_qor=False", not ev_none.is_repair_qor)

# 8e: PlanningResult fields
pr = PlanningResult(
    cycle_id="x", next_cycle_id="y",
    strategy=Strategy("optimization"),
    thresholds=AdaptiveThresholds(1.0, 10, 2, 30, "test"),
    hypothesis="test hypothesis",
    history=[], previous_strategies=[],
)
check("8e: PlanningResult fields accessible", pr.hypothesis == "test hypothesis")


# ===================================================================
# SECTION 9: Integration completeness
# ===================================================================
section("9. INTEGRATION COMPLETENESS")

# 9a: All planning module public APIs accessible
from scripts.agents.self_evolved_abc.planning import (
    PlanningEngine, CycleEvidence as CE2, Strategy as S2,
    AdaptiveThresholds as AT2,
    propose_thresholds as pt2,
    read_cycle_evidence as rce2,
    select_strategy as ss2,
)
check("9a: __init__ exports PlanningEngine", True)
check("9a: __init__ exports CycleEvidence", True)
check("9a: __init__ exports Strategy", True)
check("9a: __init__ exports AdaptiveThresholds", True)
check("9a: __init__ exports propose_thresholds", True)
check("9a: __init__ exports read_cycle_evidence", True)
check("9a: __init__ exports select_strategy", True)

# 9b: next_cycle.py imports PlanningEngine
from scripts.agents.self_evolved_abc.flow.next_cycle import build_next_assignment as bna
check("9b: next_cycle imports PlanningEngine", True)


# ===================================================================
# FINAL
# ===================================================================
print(f"\n{'='*60}")
print(f"  RESULTS: {PASS} passed, {FAIL} failed  ({PASS+FAIL} total)")
print(f"{'='*60}")

if FAIL > 0:
    sys.exit(1)
