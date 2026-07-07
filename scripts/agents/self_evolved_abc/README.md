# Self-Evolved ABC Agent Package

This package keeps orchestration code at the top level and domain-specific
implementation in focused subpackages.

## Layout

- `cycle_driver.py`: command-line entrypoint for running one assigned agent.
- `base_agent.py`, `planning_agent.py`, `model_client.py`, `cycle_context.py`,
  `schemas.py`: shared agent runtime and data contracts.
- `coding_agents/`: concrete planner-facing coding agents.
- `flow/`: Flow Agent candidate materialization, validation, evaluation, runner,
  and ABC log parsing.
- `shared/`: reusable rulebase helpers.
- `fixtures/`: local model-response fixtures for validation and smoke tests.

## Compatibility Entrypoints

- `flow_evaluation.py` forwards to `flow/evaluation.py`.
- `flow_runner.py` forwards to `flow/runner.py`.

Keep these wrappers so existing local and remote commands using
`python -m scripts.agents.self_evolved_abc.flow_runner` continue to work.

