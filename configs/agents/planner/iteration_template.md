# Paired Iteration Record Template

Use this record for every Planning round. The runtime freezes the security and
evaluation envelope; the model supplies only the round objective and two
subsystem-local hypotheses/tasks.

## Round identity

- cycle_id: `<cycle_id>`
- previous_cycle_id: `<previous_cycle_id>`
- baseline_ref: `<immutable winner/source reference>`
- evaluation_contract_hash: `<sha256>`
- planner_advice_hash: `<sha256>`

## Ordered dispatches

1. `flow_candidate_001`
   - agent: `flow_agent`
   - source root: `third_party/FlowTune/src/src/opt`
   - hypothesis/task: `<one reached flow mechanism>`
2. `logic_candidate_001`
   - agent: `logic_minimization_agent`
   - source root: `third_party/FlowTune/src/src/base/abci`
   - hypothesis/task: `<one reached technology-independent mechanism>`

## Shared metrics and stop conditions

- primary: AIG/AND node count; auxiliary: depth, runtime, skipped designs.
- compile must pass before CEC; every frozen evaluation design must have
  correctness evidence before QoR is eligible.
- stop a branch on invalid scope/diff, crash, timeout, compile failure, missing
  coverage, or CEC mismatch; allow the sibling branch to settle.
- do not merge patches. Require two valid reviews before centralized ranking.

## Required artifacts

- `experiments/<cycle>/planning/portfolio_plan.json`
- `experiments/<cycle>/planning/planner_advice.json`
- `experiments/<cycle>/planning/branch_runs/<candidate_id>.json`
- `experiments/<cycle>/planning/portfolio_review.json`
- `experiments/<cycle>/agents/{plans,candidate_changes,feedback,rule_updates}/<candidate_id>.md`
- `experiments/<cycle>/candidates/<candidate_id>/impl_compare/`
