# Feedback Schema

Use these fields when converting agent outputs and benchmark results into
machine-readable JSON. Markdown artifacts may contain the same information in
human-readable form.

## Cycle Feedback

```json
{
  "cycle_id": "cycle_001",
  "candidate_id": "candidate_001",
  "selected_agent": "flow_agent",
  "candidate_kind": "source_patch_diff",
  "benchmark_scope": ["benchmarks/epfl/epfl_adder.blif"],
  "patch_status": "PASS",
  "compile_status": "PASS",
  "smoke_status": "PASS",
  "cec_status": "PASS",
  "qor_status": "PASS",
  "primary_metric": "and_count",
  "primary_metric_delta": -127,
  "secondary_metric_delta": {"depth": 0, "runtime_seconds": 1.0},
  "accepted": true,
  "decision": "ACCEPT_FOR_NEXT_CYCLE",
  "rollback_reason": "",
  "notes": "QoR is accepted only because the matching CEC rows passed."
}
```

## Agent Feedback

```json
{
  "agent_name": "flow_agent",
  "paper_role": "Flow Agent",
  "hypothesis": "A conservative FlowTune source patch may improve one flow decision point without changing semantics.",
  "candidate_artifacts": ["experiments/cycle_001/agents/source_patches/candidate_001/patch.diff"],
  "changed_files": ["third_party/FlowTune/src/src/opt/nwk/nwkFlow.c"],
  "validation_summary": "Patch applied in isolation; candidate build, smoke, CEC, and QoR gates passed remotely.",
  "observed_regressions": [],
  "next_suggestion": "Use CEC-backed QoR rows as feedback for the next Flow Agent assignment."
}
```

## Rulebase Feedback

```json
{
  "rule_id": "R-FLOW-003",
  "action": "add",
  "reason": "The source patch produced correctness-backed QoR evidence on the small subset.",
  "evidence": "experiments/cycle_001/impl_compare/comparison/review_decision.json",
  "approved_by": "human_review"
}
```

## Status Vocabulary

- `PASS`: gate succeeded.
- `FAIL`: gate failed and requires rejection or repair.
- `SKIPPED`: gate did not run and must be explained.
- `TIMEOUT`: gate exceeded its budget.
- `NEEDS_HUMAN_REVIEW`: model or parser produced ambiguous output.
- `REPAIR_VALIDATION`: model JSON, mode, or path-scope contract failed.
- `REPAIR_PATCH`: source patch failed to apply in the isolated workspace.
- `REPAIR_SMOKE`: smoke or lightweight runner gate failed.
- `REPAIR_COMPILE`: candidate binary build failed.
- `REPAIR_EVALUATION`: remote comparison artifacts are missing or unparseable.
- `REJECT_CEC`: correctness failed or was skipped, so QoR is invalid.
- `REPAIR_QOR`: correctness passed but target QoR did not improve.
- `ACCEPT_FOR_NEXT_CYCLE`: build, CEC, and correctness-backed QoR passed.
