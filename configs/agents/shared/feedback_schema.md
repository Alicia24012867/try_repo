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
  "candidate_kind": "abc_flow",
  "benchmark_scope": ["benchmarks/epfl/epfl_adder.blif"],
  "compile_status": "SKIPPED",
  "smoke_status": "PASS",
  "cec_status": "SKIPPED",
  "qor_status": "PASS",
  "primary_metric": "and_count",
  "primary_metric_delta": -127,
  "secondary_metric_delta": {"depth": 0, "runtime_seconds": 1.0},
  "accepted": false,
  "decision": "ACCEPT_PROCESS",
  "rollback_reason": "",
  "notes": "QoR is provisional until CEC is added."
}
```

## Agent Feedback

```json
{
  "agent_name": "flow_agent",
  "paper_role": "Flow Agent",
  "hypothesis": "A conservative flow derived from cycle_000 may reduce ANDs.",
  "candidate_artifacts": ["configs/flows/cycle_001_candidate_001.abc"],
  "changed_files": [],
  "validation_summary": "Generated flow only; benchmark pending.",
  "observed_regressions": [],
  "next_suggestion": "Run the flow on the three-design EPFL subset."
}
```

## Rulebase Feedback

```json
{
  "rule_id": "R-FLOW-003",
  "action": "add",
  "reason": "The selected source script generalized to the small subset.",
  "evidence": "experiments/cycle_001/results/run_notes.md",
  "approved_by": "human_review"
}
```

## Status Vocabulary

- `PASS`: gate succeeded.
- `FAIL`: gate failed and requires rejection or repair.
- `SKIPPED`: gate did not run and must be explained.
- `TIMEOUT`: gate exceeded its budget.
- `NEEDS_HUMAN_REVIEW`: model or parser produced ambiguous output.

