# Implementation Compare Summary -- cycle_001 candidate_001

## Decision Gate

- Candidate build status: `build_smoke_passed`
- QoR rows complete: 0/3
- CEC pass: 0/3
- Correctness-backed delta rows: 0/3
- Average AND improvement pct: ``
- Promotion allowed: `false`

## Artifacts

- `baseline_flow_summary.csv`
- `candidate_flow_summary.csv`
- `cec_summary.csv`
- `qor_delta.csv`
- logs under `../baseline_unmodified/logs/`, `../candidate_modified/logs/`, and `logs/`
- AIG outputs under `../baseline_unmodified/outputs/` and `../candidate_modified/outputs/`

## Policy

- QoR deltas are reviewable only when `correctness_backed` is true.
- Any CEC fail, timeout, crash, skip, or unparseable result blocks promotion.
- This runner does not update the active rulebase.
