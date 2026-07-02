# Agent Architecture

This directory is a TODO scaffold for the paper-style multi-agent ABC evolution
loop. It defines the roles, boundaries, prompts, and feedback contracts, but it
does not implement autonomous code editing yet.

## Roles

- `planner/`: TODO global planning agent for cycle-level decisions.
- `coding/flow_tuning_agent.md`: TODO coding agent for FlowTune and flow
  scheduling.
- `coding/logic_minimization_agent.md`: TODO coding agent for AIG synthesis,
  rewrite, refactor, resubstitution, and orchestration.
- `coding/mapping_agent.md`: TODO coding agent for technology mapping.
- `shared/`: TODO shared programming guidance, rulebase, and feedback schemas.
- `prompts/`: TODO prompt templates for planning, coding, repair, and review.
- `checklists/`: TODO human-readable gates for compile, CEC, and QoR checks.
- `templates/`: copyable reference templates for adding new agents cleanly.

## Cycle Flow

1. TODO knowledge bootstrap and repository profiling.
2. TODO planner proposes the next cycle objective.
3. TODO coding agents propose scoped changes.
4. TODO compile and smoke checks.
5. TODO formal equivalence checking.
6. TODO benchmark evaluation.
7. TODO reward calculation and rulebase update.

## Current Scope

For the small reproduction, keep all files as TODO placeholders and use them to
document decisions before any automated source-code evolution is attempted.
