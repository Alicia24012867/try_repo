# Multi-Agent Architecture

This configuration follows the paper's role split while keeping the first
reproduction small enough to complete locally and then run remotely.

## Paper Mapping

- Planning Agent: owns the cycle objective, agent selection, allowed scope,
  benchmark subset, risk controls, rollback policy, and promotion criteria.
- Flow Agent: owns flow scheduling, FlowTune-derived scripts, pass selection,
  sampling policy, stopping criteria, and flow-level diagnostics.
- Logic Minimization Agent: owns technology-independent AIG optimization,
  including rewrite, refactor, resubstitution, and orchestration heuristics.
- Mapper Agent: owns technology mapping heuristics, including cut enumeration,
  cut pruning, cut ranking, and area/depth/delay tie-breaking.
- Self-Evolved Rulebase: stores reusable rules learned from accepted and
  rejected candidates.
- Evaluation Loop: compiles, checks correctness, runs benchmarks, aggregates
  QoR, and supplies feedback to the next cycle.

## Runtime Scaffold

- `scripts/agents/self_evolved_abc/planning_agent.py`: Planning Agent scaffold.
- `scripts/agents/self_evolved_abc/coding_agents/flow_agent.py`: Flow Agent
  scaffold.
- `scripts/agents/self_evolved_abc/coding_agents/logic_minimization_agent.py`:
  Logic Minimization Agent scaffold.
- `scripts/agents/self_evolved_abc/coding_agents/mapper_agent.py`: Mapper Agent
  scaffold.
- `scripts/agents/self_evolved_abc/shared/rulebase.py`: rulebase scaffold.
- `scripts/agents/self_evolved_abc/model_client.py`: LLM API boundary.
- `scripts/agents/self_evolved_abc/cycle_driver.py`: one-agent execution
  entry point for a rendered assignment.

## Data Flow

1. `scripts/summarize_cycle.py` converts raw logs into structured evidence.
2. `scripts/init_cycle.py` creates the next cycle assignment.
3. `cycle_driver.py` loads the assignment and selects the paper-role agent.
4. The selected agent reads evidence and renders a prompt from `configs/agents`.
5. `model_client.py` sends the prompt to the configured model and returns JSON.
6. The agent validates the JSON and writes cycle artifacts.
7. The benchmark harness runs the candidate and stores logs/outputs/results.
8. The review prompt decides acceptance, repair, rejection, or deferral.

## Subsystem Boundaries

- Flow Agent:
  - Read: `experiments/<previous>/results/`, `experiments/<previous>/outputs/`,
    `configs/flows/`, FlowTune command documentation.
  - First-cycle write: `configs/flows/` and `experiments/<cycle>/agents/`.
  - Later-cycle source boundary: `third_party/FlowTune/src/opt/flowtune/` if
    source edits are explicitly enabled.
- Logic Minimization Agent:
  - Later-cycle source boundary: ABC/FlowTune AIG and command orchestration
    modules under `third_party/FlowTune/src/`.
  - No sequential behavior changes unless the planner creates a dedicated
    sequential experiment.
- Mapper Agent:
  - Later-cycle source boundary: mapper modules under
    `third_party/FlowTune/src/map/`.
  - No library, GENLIB, Liberty, or benchmark edits.

## Safety Contract

- Agents must stay within the assignment's `allowed_to_edit` paths.
- Generated candidates must be reversible and attributable to one hypothesis.
- Benchmarks, raw previous-cycle logs, and previous-cycle result tables are
  read-only evidence.
- Compile and smoke gates precede benchmark evaluation.
- CEC is required before QoR can be considered final.
- Any skipped design must be listed with an explicit reason.
- A candidate that improves one design by hard-coding names is rejected.
- Rulebase updates must cite evidence from a cycle artifact.

