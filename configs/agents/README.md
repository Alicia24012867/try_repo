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
2. Build an assignment under `experiments/<cycle>/agents/assignments/`.
3. Render the Planning Agent prompt with the previous cycle evidence and
   current rulebase.
4. Dispatch one scoped coding agent.
5. Require the coding agent to produce a candidate plan, candidate artifact,
   feedback, and rulebase update proposal.
6. Run compile, smoke, CEC, and QoR gates outside the prompt layer.
7. Review the candidate and decide whether to accept, repair, reject, or defer.
8. Update the rulebase only after the evidence is reviewable.

## First-Cycle Reproduction Profile

For `cycle_001`, keep the scope intentionally small:

- Agent: Flow Agent.
- Benchmark subset: `epfl_adder`, `epfl_bar`, and `epfl_sqrt`.
- Candidate type: ABC flow script under `configs/flows/`.
- Source edits: none.
- Required evidence: `cycle_000` summary, skipped table, run notes, and
  selected FlowTune scripts.
- Correctness caveat: QoR is provisional until a CEC gate is added.

