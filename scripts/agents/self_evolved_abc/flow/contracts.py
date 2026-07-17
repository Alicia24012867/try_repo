"""Paper-aligned constants for the Flow Agent evolution loop."""

from __future__ import annotations

from pathlib import Path


ABC_RC_PATH = Path("third_party") / "FlowTune" / "abc.rc"
DEFAULT_ABC_BIN = Path("third_party") / "FlowTune" / "FlowTune-OpenFPGA" / "abc"
FLOWTUNE_SOURCE_ROOT = Path("third_party") / "FlowTune" / "src"
FLOWTUNE_SOURCE_ABC_BIN = FLOWTUNE_SOURCE_ROOT / "abc"

FLOW_CANDIDATE_ABC_FLOW = "abc_flow"
FLOW_CANDIDATE_SOURCE_PATCH_TODO = "source_patch_todo"
FLOW_CANDIDATE_SOURCE_PATCH_DIFF = "source_patch_diff"
FLOW_CANDIDATE_DIAGNOSTIC_ONLY = "diagnostic_only"
FLOW_CANDIDATE_KINDS = (
    FLOW_CANDIDATE_ABC_FLOW,
    FLOW_CANDIDATE_SOURCE_PATCH_TODO,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_CANDIDATE_DIAGNOSTIC_ONLY,
)
FLOW_DECISIONS = (
    "PROPOSE_CANDIDATE",
    "NEEDS_PLANNER_APPROVAL",
    "DEFER",
    "NEEDS_HUMAN_REVIEW",
)

FLOW_RECIPE_BASELINE_LABEL = "vanilla_strash"
FLOW_RECIPE_CANDIDATE_LABEL = "candidate_flow"
IMPL_BASELINE_LABEL = "baseline_unmodified"
IMPL_CANDIDATE_LABEL = "candidate_modified"
IMPL_COMPARISON_LABEL = "comparison"

CORRECTNESS_PROVISIONAL = "provisional_no_cec"
PATCH_DIFF_NAME = "patch.diff"
SOURCE_PATCH_TARGET_SECTION = "Proposed Target Files"
SMOKE_GATE_COMMAND_LABEL = "python_smoke_gate"
CANDIDATE_BINARY_BUILD_COMMAND_LABEL = "flowtune_candidate_make"
CANDIDATE_BUILD_READY_STATUSES = (
    "candidate_binary_build_passed",
)

FLOW_INFRA_ALLOWED_ROOTS = (
    "scripts/agents/self_evolved_abc/flow",
    "scripts/agents/self_evolved_abc/coding_agents/flow_agent.py",
    "configs/agents/prompts",
)
FLOWTUNE_SOURCE_SCOPE_PRIMARY = "third_party/FlowTune/src/src/opt"
FLOWTUNE_ABCI_SCOPE = "third_party/FlowTune/src/src/base/abci"
FLOW_SOURCE_PATCH_DIFF_ALLOWED_ROOTS = (
    FLOWTUNE_SOURCE_SCOPE_PRIMARY,
    FLOWTUNE_ABCI_SCOPE,
    "third_party/FlowTune/src/src/map/mapper",
)
FLOW_SOURCE_PATCH_TODO_ALLOWED_ROOTS = FLOW_INFRA_ALLOWED_ROOTS

LEGACY_EVAL_FLOW_COMMANDS = (
    "strash",
    "rewrite -z",
    "resub -K 8",
    "dc2",
    "refactor -z",
    "strash",
    "print_stats",
)

DEFAULT_EVAL_FLOW_COMMANDS = (
    "fx",
    "strash",
    "rewrite -z",
    "resub -K 8",
    "dc2",
    "csweep",
    "refactor -z",
    "strash",
    "print_stats",
)

FLOW_SOURCE_TOUCHPOINTS = {
    "fx": [
        "third_party/FlowTune/src/src/opt/fxu",
        "third_party/FlowTune/src/src/base/abci/abcFx.c",
        "third_party/FlowTune/src/src/base/abci/abcFxu.c",
    ],
    "rewrite": [
        "third_party/FlowTune/src/src/opt/rwr",
        "third_party/FlowTune/src/src/base/abci/abcRewrite.c",
    ],
    "resub": [
        "third_party/FlowTune/src/src/opt/res",
        "third_party/FlowTune/src/src/base/abci/abcResub.c",
    ],
    "dc2": [
        "third_party/FlowTune/src/src/opt/dar",
        "third_party/FlowTune/src/src/base/abci/abcDar.c",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ],
    "refactor": [
        "third_party/FlowTune/src/src/base/abci/abcRefactor.c",
        "third_party/FlowTune/src/src/opt/dar",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ],
    "csweep": [
        "third_party/FlowTune/src/src/opt/csw",
        "third_party/FlowTune/src/src/base/abci/abcDar.c",
        "third_party/FlowTune/src/src/base/abci/abc.c",
    ],
}

PYTHON_SMOKE_FILES = (
    "scripts/agents/self_evolved_abc/benchmarks.py",
    "scripts/agents/self_evolved_abc/flow/assignment.py",
    "scripts/agents/self_evolved_abc/flow/source_patch_runner.py",
    "scripts/agents/self_evolved_abc/flow/source_patch.py",
    "scripts/agents/self_evolved_abc/flow/materialization.py",
    "scripts/agents/self_evolved_abc/flow/multi_flow.py",
    "scripts/agents/self_evolved_abc/flow/validation.py",
    "scripts/agents/self_evolved_abc/flow/verilog_frontend.py",
    "scripts/agents/self_evolved_abc/flow/artifacts.py",
    "scripts/agents/self_evolved_abc/flow/runner.py",
    "scripts/agents/self_evolved_abc/flow/evaluation.py",
    "scripts/agents/self_evolved_abc/flow/lineage.py",
    "scripts/agents/self_evolved_abc/flow/promotion.py",
    "scripts/agents/self_evolved_abc/flow/planner_batch.py",
    "scripts/agents/self_evolved_abc/flow/batch_search.py",
    "scripts/agents/self_evolved_abc/flow/implementation_compare.py",
    "scripts/agents/self_evolved_abc/flow/iteration_loop.py",
    "scripts/agents/self_evolved_abc/flow/next_cycle.py",
    "scripts/agents/self_evolved_abc/flow/review.py",
    "scripts/agents/self_evolved_abc/logic/contracts.py",
    "scripts/agents/self_evolved_abc/logic/assignment.py",
    "scripts/agents/self_evolved_abc/roles/registry.py",
    "scripts/agents/self_evolved_abc/planning/assignment_factory.py",
    "scripts/agents/self_evolved_abc/planning/portfolio.py",
    "scripts/agents/self_evolved_abc/workflow/artifacts.py",
    "scripts/agents/self_evolved_abc/workflow/branch_run.py",
    "scripts/agents/self_evolved_abc/workflow/candidate_pipeline.py",
    "scripts/agents/self_evolved_abc/workflow/dual_agent_loop.py",
    "scripts/agents/self_evolved_abc/workflow/evaluation_recipe.py",
    "scripts/agents/self_evolved_abc/workflow/portfolio_review.py",
    "scripts/agents/self_evolved_abc/flow_runner.py",
    "scripts/agents/self_evolved_abc/flow_evaluation.py",
    "scripts/agents/self_evolved_abc/cycle_context.py",
    "scripts/agents/self_evolved_abc/coding_agents/flow_agent.py",
    "scripts/agents/self_evolved_abc/coding_agents/logic_minimization_agent.py",
    "scripts/agents/self_evolved_abc/cycle_driver.py",
    "scripts/agents/self_evolved_abc/planning_agent.py",
)

VALIDATION_FIXTURE_EXPECTATIONS = (
    ("flow_valid_abc_flow.json", True),
    ("flow_defer.json", True),
    ("flow_invalid_missing_candidate_steps.json", False),
    ("flow_invalid_path_escape.json", False),
    ("flow_invalid_shell_command.json", False),
    ("flow_valid_source_patch_todo.json", True),
    ("flow_invalid_source_patch_missing_contract.json", False),
    ("flow_invalid_source_patch_missing_validation_plan.json", False),
    ("flow_invalid_source_patch_path_scope.json", False),
    ("flow_invalid_source_patch_shell_step.json", False),
    ("flow_valid_source_patch_diff.json", True),
    ("flow_invalid_source_patch_diff_missing_payload.json", False),
    ("flow_invalid_source_patch_diff_path_scope.json", False),
)
