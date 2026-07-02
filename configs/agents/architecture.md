# Multi-Agent Architecture

## Paper Mapping

- Planning Agent: TODO owns cycle objectives, subsystem selection, rollback
  policy, and global QoR interpretation.
- Flow Agent: TODO owns flow scheduling and FlowTune-related logic.
- Logic Minimization Agent: TODO owns technology-independent AIG optimization.
- Mapping Agent: TODO owns mapper heuristics and cost-model refinements.
- Self-Evolved Rulebase: TODO records constraints and rule updates across
  cycles.
- Evaluation Loop: TODO compiles, checks correctness, runs benchmarks, and
  aggregates QoR feedback.

## Subsystem Boundaries

- `src/opt/flowtune/`: TODO Flow Agent boundary.
- `src/base/abci/`: TODO Logic Minimization Agent boundary.
- `src/map/mapper/`: TODO Mapping Agent boundary.

## Safety Contract

- TODO agents must not edit outside their assigned subsystem without planner
  approval.
- TODO every candidate version must compile before evaluation.
- TODO every candidate version must pass CEC before QoR results are accepted.
- TODO regressions must be logged with enough context for rollback.
