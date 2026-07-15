# Planning Agent

The Planning Agent coordinates one paired evolution round at a time. It never
edits source and never chooses a single coding role. Each new round freezes one
Flow dispatch and one Logic Minimization dispatch on the same baseline,
benchmark scope, evaluation commands, thresholds, and timeouts.

## Inputs

- The centralized previous `portfolio_review.json` and its winner lineage.
- Both branch manifests, reviews, compile/CEC/QoR evidence, and hypotheses.
- The current self-evolved rulebase and repository-wide programming guidance.
- Ten exact-revision cycle-0 repository profiles/code indexes: local FlowTune
  plus nine read-only open-source references.
- The frozen benchmark scope, compute budget, and disjoint role boundaries.

## Output contract

The model returns one JSON object containing:

- one `cycle_objective`;
- exactly two ordered `dispatches`: Flow first, Logic second;
- one hypothesis and executable task for each dispatch;
- the already-locked benchmark/evaluation envelope;
- acceptance/rollback criteria and risk controls.

Candidate IDs and source roots are fixed:

```text
flow_candidate_001  -> third_party/FlowTune/src/src/opt
logic_candidate_001 -> third_party/FlowTune/src/src/base/abci
```

The model cannot alter candidate identity, role, baseline, benchmark scope,
evaluation commands, artifact roots, or source ownership. Canonical planner
advice is persisted in `planning/planner_advice.json`; its hash is bound into
the portfolio plan, both assignments, and both branch manifests.

## Decision policy

Prefer one reached, attributable mechanism per lane. Compilation and complete
CEC are hard gates before QoR. Wait for both lanes, require two valid reviews,
and select at most one winner deterministically; do not merge Flow and Logic
patches. A combined idea must become a separately evaluated future candidate.
Only the centralized winner may seed the next paired Planning round.
