# Agent Configuration

This directory contains the paper-facing configuration for the small
Multi-Agent Self-Evolved ABC reproduction. It defines the agent roles, prompts,
rulebase, validation contracts, and review checklists used by the executable
LLM scaffold under `scripts/agents/self_evolved_abc/`.

The files here are configuration and operating doctrine. They do not call an
LLM API and they do not modify ABC or FlowTune source code.

## Directory Roles

- `planner/`: cycle-level planning policy, iteration record format, and the
  expected inputs/outputs for the Planning Agent.
- `coding/`: role cards for the Flow Agent, Logic Minimization Agent, and
  Mapper Agent.
- `prompts/`: full prompt templates for planning, candidate generation, repair,
  and review. Placeholders such as `{{CYCLE_ID}}` are filled by the runtime
  scaffold before the prompt is sent to a model.
- `shared/`: shared programming guidance, evaluation contract, feedback schema,
  and self-evolved rulebase.
- `checklists/`: human-readable gates for compile, CEC, and QoR review.

## Runtime Mapping

- `scripts/agents/self_evolved_abc/planning_agent.py` consumes the planner
  prompt and emits cycle objectives.
- `scripts/agents/self_evolved_abc/coding_agents/flow_agent.py` consumes the
  coding prompt with Flow Agent constraints.
- `scripts/agents/self_evolved_abc/coding_agents/logic_minimization_agent.py`
  consumes the coding prompt with AIG optimization constraints.
- `scripts/agents/self_evolved_abc/coding_agents/mapper_agent.py` consumes the
  coding prompt with mapping constraints.
- `scripts/agents/self_evolved_abc/model_client.py` is the only intended place
  for LLM API integration.

## Cycle Flow

1. Parse the previous cycle into `summary.csv`, `skipped.csv`, and
   `run_notes.md`.
2. Build an assignment under `experiments/<cycle>/agents/assignments/`;
   Flow Agent assignments are normalized by `flow/assignment.py` so source-patch
   scope and active-cycle artifact paths stay consistent.
3. Render the Planning Agent prompt with the previous cycle evidence and
   current rulebase.
4. Dispatch one scoped coding agent.
5. Require the coding agent to produce a candidate plan, candidate artifact,
   feedback, and rulebase update proposal.
6. Run compile, smoke, CEC, and QoR gates outside the prompt layer.
7. Review the candidate and decide whether to accept, repair, reject, or defer.
8. Update the rulebase only after the evidence is reviewable.

## Current Flow-Agent Reproduction Profile

For `cycle_001`, keep the benchmark scope intentionally small while exercising
the source-level feedback loop:

- Agent: Flow Agent.
- Benchmark subset: `epfl_adder`, `epfl_bar`, and `epfl_sqrt`.
- Candidate type: `source_patch_diff`.
- Source patch scope: `third_party/FlowTune/src/src/opt`.
- Required evidence: `cycle_000` summary, skipped table, run notes, and
  selected FlowTune scripts.
- Correctness policy: QoR is not trusted unless the remote S5/F7 comparison
  produces CEC-backed delta rows.

The earlier `.abc` flow path remains available for fixtures and legacy
flow-recipe evaluation, but the current autonomous loop targets FlowTune source
patches.
