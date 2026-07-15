# Paired Feedback Schema

Each candidate lane writes an independent, candidate-scoped review. The
coordinator then writes one portfolio review only after both lane reviews pass
lineage and coverage validation.

## Branch feedback

```json
{
  "cycle_id": "cycle_001",
  "candidate_id": "flow_candidate_001",
  "agent_name": "flow_agent",
  "candidate_kind": "source_patch_diff",
  "assignment_hash": "<sha256>",
  "evaluation_contract_hash": "<sha256>",
  "planner_advice_hash": "<sha256>",
  "patch_status": "PASS",
  "compile_status": "PASS",
  "cec_status": "PASS",
  "cec_pass_count": 30,
  "cec_total_count": 30,
  "correctness_backed_rows": 30,
  "qor_status": "PASS",
  "scalar_and_reward": 127,
  "improved_benchmark_count": 4,
  "regressed_benchmark_count": 0,
  "decision": "ACCEPT_FOR_NEXT_CYCLE",
  "promotion_allowed": true
}
```

## Portfolio feedback

```json
{
  "cycle_id": "cycle_001",
  "status": "complete",
  "quorum_required": 2,
  "quorum_observed": 2,
  "winner_candidate_id": "flow_candidate_001",
  "winner_source_root": "experiments/cycle_001/candidates/flow_candidate_001/impl_compare/candidate_modified/workspace/third_party/FlowTune/src",
  "decision": "PROMOTE_ONE",
  "implicit_merge": false
}
```

`winner_candidate_id` is empty when neither candidate is eligible or when all
ranking metrics tie exactly. A failed/missing branch, stale hash, incomplete
CEC coverage, or nonzero pipeline result prevents quorum and therefore prevents
promotion and next-cycle generation.

## Status vocabulary

- `PASS`, `FAIL`, `SKIPPED`, `TIMEOUT`: gate results.
- `REPAIR_VALIDATION`, `REPAIR_PATCH`, `REPAIR_SMOKE`, `REPAIR_COMPILE`:
  candidate construction failures.
- `REPAIR_EVALUATION`, `REJECT_CEC`, `REPAIR_QOR`: evidence/QoR failures.
- `ACCEPT_FOR_NEXT_CYCLE`: branch is eligible for portfolio ranking only.
- `PROMOTE_ONE`, `NO_WINNER`, `QUORUM_FAILED`: centralized outcomes.
