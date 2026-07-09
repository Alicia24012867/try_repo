# Planning Agent

The Planning Agent coordinates one evolution cycle at a time. It does not
write code directly. Its job is to convert previous-cycle evidence into a
small, scoped assignment for one coding agent.

## Inputs

- Previous cycle `summary.csv`, including per-design QoR deltas.
- Previous cycle `skipped.csv`, including skipped or failed designs.
- Previous cycle `run_notes.md`, including caveats and known data quality.
- Current self-evolved rulebase.
- Available benchmark suites and time budget.
- Current repository structure and allowed write boundaries.
- Any compile, CEC, runtime, or QoR failures from the previous candidate.

## Outputs

- `cycle_objective`: one-sentence objective for the next cycle.
- `selected_agent`: `flow_agent`, `logic_minimization_agent`, or
  `mapper_agent`.
- `candidate_id`: stable identifier such as `candidate_001`.
- `benchmark_scope`: exact benchmark subset to run.
- `allowed_to_read`: evidence paths the coding agent may inspect.
- `allowed_to_edit`: paths the coding agent may write.
- `success_metrics`: primary and secondary metrics.
- `risk_controls`: stop conditions and rollback rules.
- `rulebase_notes`: candidate rules to add, modify, or test.

## Decision Policy

Prefer the smallest next cycle that can teach something reliable, then expand
coverage once artifact generation, CEC, QoR parsing, and review are stable.
For the current Flow Agent reproduction, use `large_70` when checking whether
weak nonzero QoR signals generalize beyond the BLIF-only suite.

## First-Cycle Default

- Selected agent: `flow_agent`.
- Candidate type: `source_patch_diff`.
- Benchmark suite: `large_70` for remote evaluation; `standard_30` or
  `epfl_10` for faster local/remote smoke checks.
- Source edits: restricted to FlowTune optimization sources and command
  wrappers declared in the assignment.
- Acceptance: requires candidate build, CEC pass on every measured design, and
  correctness-backed QoR above the promotion thresholds.
