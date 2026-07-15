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
- `context/`: pinned related-repository manifest and checked-in profiles used
  for bounded, read-only prompt code indexing.

## Knowledge-Bootstrap Contract

The paper treats repository knowledge as a cycle-0 input: Figure 1 and Section
3.1 profile ABC and related repositories, Section 3.3 supplies the generated
repository profile and structured Markdown tutorial, and Section 4.2 attributes
68% of token use to ABC profiling plus 11% to external codebase profiling.
Accordingly, the runtime does not rely on repository names alone.

- `context/repositories.json` pins ten exact commits and the paths worth
  indexing.
- `repository_context.py` selects role/query-relevant source windows under a
  hard character budget (96,000 for Planning, 72,000 for Logic, 60,000 for
  Flow; never more than 160,000).
- Planning sees all ten pinned repositories. Nine external repositories are
  eligible for Logic prompt excerpts; six are routed to Flow. FlowTune remains
  the separately indexed local build/API source for Coding Agents.
- The bootstrap minimum is ten ready repositories. Strict role assignments
  require the complete subset routed to that agent.
- Missing, dirty, incomplete, or revision-mismatched checkouts degrade to their
  checked-in profile only. They never contribute source text. When minimum
  enforcement is enabled, that degradation becomes an actionable failure with
  the exact bootstrap command.
- Manifest, profile, checkout, and focus paths must remain inside the project;
  external repository paths never become candidate write scope.

See `context/README.md` for the inventory, budgets, trust model, and failure
matrix.

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
   Flow Agent assignments are normalized by `flow/assignment.py`; Logic Agent
   assignments use `logic/assignment.py` so each role has a hard source ceiling
   and consistent active-cycle artifact paths.
3. Render the Planning Agent prompt with the previous cycle evidence and
   current rulebase.
4. Freeze exactly one Flow and one Logic assignment on the same baseline and
   evaluation contract, then dispatch both isolated lanes concurrently.
5. Require each coding agent to produce a candidate plan, candidate artifact,
   feedback, and rulebase update proposal.
6. Run compile, smoke, CEC, and QoR gates independently in each lane.
7. Wait for both lanes, require a strict review quorum, and write one
   deterministic portfolio decision; never merge patches implicitly.
8. Start the next Planning round only from the centralized winner lineage.

## Current Flow-Agent Reproduction Profile

For `cycle_001`, use the source-level feedback loop with a named benchmark
suite:

- Agent: Flow Agent.
- Benchmark suite: `large_70` for broader remote tracking. Current
  ABC-native promotion evaluates the 30 BLIF designs in that scope and records
  the 40 Verilog designs as frontend-pending; use `standard_30` or `epfl_10`
  only for faster smoke/debug runs.
- Candidate type: `source_patch_diff`.
- Source patch scope: `third_party/FlowTune/src/src/opt` plus command wrappers
  under `third_party/FlowTune/src/src/base/abci`.
- Default evaluation flow: `fx; strash; rewrite -z; resub -K 8; dc2; csweep;
  refactor -z; strash; print_stats`.
- Required evidence: previous review decision, CEC/QoR deltas, feedback,
  rule-update notes, and selected FlowTune source excerpts.
- Correctness policy: QoR is not trusted unless the remote S5/F7 comparison
  produces CEC-backed delta rows.

The earlier `.abc` flow path remains available for fixtures and legacy
flow-recipe evaluation, but the current autonomous loop targets FlowTune source
patches.

## Logic-Minimization Reproduction Profile

- Agent: Logic Minimization Agent / paper AIG Syn role.
- Candidate type: `source_patch_diff`.
- Default source boundary: `third_party/FlowTune/src/src/base/abci`.
- Operations: rewrite, refactor, resubstitution, balancing, `dc2`, and
  conservative orchestration of existing safe commands.
- Gate order: isolated patch application and compile, smoke, CEC/`dsat`, then
  multi-design AIG node/depth/runtime QoR.
- Knowledge layer: nine pinned external repositories with query-relevant,
  read-only excerpts, plus the separately indexed local FlowTune build source.
  Bootstrap verifies all ten exact, clean revisions.

Provision or verify that context with:

```bash
python3 -B scripts/bootstrap_agent_context.py
python3 -B scripts/bootstrap_agent_context.py --check
```
