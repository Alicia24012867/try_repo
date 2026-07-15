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
  executable Logic Minimization Agent with strict source-patch validation.
- `scripts/agents/self_evolved_abc/coding_agents/mapper_agent.py`: Mapper Agent
  scaffold.
- `scripts/agents/self_evolved_abc/shared/rulebase.py`: rulebase scaffold.
- `scripts/agents/self_evolved_abc/model_client.py`: LLM API boundary.
- `scripts/agents/self_evolved_abc/workflow/dual_agent_loop.py`: primary
  Planning -> (Flow || Logic) -> portfolio-review loop.
- `scripts/agents/self_evolved_abc/cycle_driver.py`: legacy single-assignment
  execution entry point used inside a candidate lane and by diagnostics.

## Data Flow

1. Planning reads the previous centralized review and frozen champion lineage.
2. It writes one immutable Flow assignment and one immutable Logic assignment
   with the same baseline and evaluation-contract hash.
3. The two candidate pipelines render role prompts and call their coding agents
   concurrently in non-overlapping source workspaces.
4. Each lane validates its JSON/diff, compiles, runs full-scope CEC, evaluates
   QoR, and writes a lineage-bound review plus branch manifest.
5. The coordinator waits for both lanes and requires a strict review quorum.
6. `portfolio_review.py` deterministically selects at most one winner; it never
   merges the two source patches.
7. Only the centralized winner may seed the next Planning round.

## Subsystem Boundaries

- Flow Agent:
  - Read: `experiments/<previous>/results/`, `experiments/<previous>/outputs/`,
    `configs/flows/`, FlowTune command documentation.
  - Active-cycle artifact write: `experiments/<cycle>/agents/`,
    `experiments/<cycle>/impl_compare/`, logs, outputs, and results.
  - Current source-patch boundary: `third_party/FlowTune/src/src/opt/` when
    `source_patch_mode` is `source_patch_diff`.
  - Legacy flow-recipe write: `configs/flows/` when `source_patch_mode` is
    `abc_flow`.
- Logic Minimization Agent:
  - Default source boundary: `third_party/FlowTune/src/src/base/abci`.
  - `opt/rwr`, `opt/res`, and `opt/dar` require explicit planner approval;
    mapping and sequential roots are outside the role ceiling.
  - No retiming, latch/register, initial-state, or other sequential changes.
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
- Related repositories are pinned, profiled, indexed, and injected as read-only
  prompt evidence; their paths never enter `allowed_to_edit`.

## Pre-Evolution Knowledge Boundary

```text
repositories.json + checked-in profiles
                 │
                 ├─ bootstrap_agent_context.py ─ exact/clean/complete check
                 │
                 └─ repository_context.py ─ role/query ranking ─ hard budget
                                                   │
                                      EXTERNAL_REPOSITORY_CONTEXT
```

The manifest has a ten-repository bootstrap minimum. Planning receives all ten
pinned profiles/code indexes, including the local FlowTune build source; Logic
routes nine external references; Flow routes six. Coding prompts keep FlowTune
in their separate local source index because it is also the compiled candidate
tree. A checkout may
contribute code only when its HEAD equals the full pinned commit, its focus
paths exist, and `git status --porcelain` is empty. Otherwise the prompt labels
the exact failure and includes only the tracked profile; strict minimum policy
may stop model invocation. Repository text is untrusted data and cannot alter
the agent role, edit scope, or validation gates.
