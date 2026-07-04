# Iteration Record Template

Use this record format for every cycle-level plan. The Planning Agent may emit
it directly, or the driver may reconstruct it from the model's JSON response.

## Cycle

- cycle_id: `<cycle_id>`
- previous_cycle_id: `<previous_cycle_id>`
- candidate_id: `<candidate_id>`
- selected_agent: `<flow_agent | logic_minimization_agent | mapper_agent>`

## Planner Objective

State one measurable objective. Example: "Use cycle_000 FlowTune evidence to
propose one conservative flow candidate for three EPFL designs."

## Hypothesis

Describe the causal mechanism, not only the desired metric movement. Example:
"A script derived from a stable positive-improvement EPFL design may reduce AIG
node count on related arithmetic/control designs without changing depth."

## Allowed Scope

- allowed_to_read:
  - previous-cycle results
  - previous-cycle outputs
  - current prompt/config files
- allowed_to_edit:
  - current-cycle agent artifacts
  - current-cycle logs/outputs/results
  - `configs/flows/` for flow-only candidates

## Metrics

- primary_metric: AIG AND count
- secondary_metrics: AIG depth, runtime, skipped designs, crash/assertion count
- correctness_metric: CEC status when available

## Stop Conditions

- Stop on compile failure.
- Stop on ABC assertion, crash, or missing output.
- Stop on CEC mismatch.
- Stop on repeated timeout.
- Stop if the candidate requires a path outside `allowed_to_edit`.

## Rollback Criteria

- Roll back on correctness failure.
- Roll back on broad QoR regression without a compensating primary-metric gain.
- Roll back if the candidate is not attributable to one hypothesis.
- Roll back if generated artifacts cannot be reproduced from the assignment.

## Required Artifacts

- `experiments/<cycle>/agents/plans/<candidate_id>.md`
- `experiments/<cycle>/agents/candidate_changes/<candidate_id>.md`
- `experiments/<cycle>/agents/feedback/<candidate_id>.md`
- `experiments/<cycle>/agents/rule_updates/<candidate_id>.md`

