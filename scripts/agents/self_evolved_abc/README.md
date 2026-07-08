# Self-Evolved ABC Agent Package

This package keeps orchestration code at the top level and domain-specific
implementation in focused subpackages.

## Layout

- `cycle_driver.py`: command-line entrypoint for running one assigned agent.
- `base_agent.py`, `planning_agent.py`, `model_client.py`, `cycle_context.py`,
  `schemas.py`: shared agent runtime and data contracts.
- `coding_agents/`: concrete planner-facing coding agents.
- `flow/`: Flow Agent validation, materialization, isolated patch application,
  build/smoke gating, CEC-first implementation comparison, review feedback, and
  next-cycle handoff.
- `shared/`: reusable rulebase helpers.
- `fixtures/`: local model-response fixtures for validation and smoke tests.

## Flow Agent Subpackage

The `flow/` package follows the paper's evolution loop:

- `contracts.py`: shared paper-facing labels, candidate kinds, source scopes,
  smoke files, and fixture expectations.
- `assignment.py`: active-cycle directory list, Flow Agent edit-scope
  construction, and `source_patch_mode`/subsystem normalization.
- `paths.py`: canonical cycle, result, and implementation-comparison paths.
- `command_io.py`: shared command log format used by local and remote runners.
- `validation.py`: Flow Agent JSON/schema/scope validation, including code-level
  enforcement that `source_patch_mode` matches `candidate_kind` for materialized
  proposals.
- `materialization.py`, `source_patch.py`: `.abc` flow and source-patch artifact
  materialization without direct source-tree mutation.
- `source_patch_runner.py`: S4 manifests, isolated patch application,
  build/smoke gate, and optional candidate ABC binary build inside the
  workspace.
- `implementation_compare.py`: S5/F7 CEC-first baseline/candidate comparison.
- `review.py`: build/CEC/QoR feedback and rule-update proposal generation.
- `next_cycle.py`, `iteration_loop.py`: feedback handoff into the next cycle.
- `evaluation.py`, `runner.py`, `metrics.py`: flow-recipe evaluation and ABC log
  parsing used by the earlier flow-only path.

## Local Versus Remote Execution

Local development should stay lightweight: run Python compilation, fixture
validation, and assignment normalization checks. Full candidate binary builds,
ABC execution, CEC, and QoR comparison are intended for the remote Linux/ABC
host after rsync. The local macOS workspace may contain a Linux FlowTune binary,
which is useful for remote runs but not locally executable.

## Compatibility Entrypoints

- `flow_evaluation.py` forwards to `flow/evaluation.py`.
- `flow_runner.py` forwards to `flow/runner.py`.

Keep these wrappers so existing local and remote commands using
`python -m scripts.agents.self_evolved_abc.flow_runner` continue to work.
