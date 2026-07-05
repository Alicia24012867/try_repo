# cycle_001 Flow Results

This directory receives synchronized results from the execution host.
F5 defines the contract; F6 or later scripts may populate the files.

## Required Files

- `flow_summary.csv`: one row per benchmark and flow label.
- `run_notes.md`: short human-readable summary of skips, failures, and caveats.
- Optional raw logs are stored under `../logs/`.
- AIG outputs are stored under `../outputs/` for later CEC.

## CSV Header

```text
benchmark,flow_label,abc_exit_code,aig_nodes,aig_depth,runtime_seconds,skipped_reason,correctness_status
```

## Status Rules

- Use `provisional_no_cec` until automated or manual CEC has passed.
- Use `not_run` when a command did not execute.
- Fill `skipped_reason` for every skipped, timed-out, crashed, or unparseable case.
- Do not use provisional QoR rows as champion-promotion evidence.
