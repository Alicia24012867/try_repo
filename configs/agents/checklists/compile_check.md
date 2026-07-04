# Compile Check

Use this checklist before running QoR benchmarks for any source-changing
candidate.

## Required Inputs

- cycle id
- candidate id
- source tree path
- build command
- expected binary path
- timeout
- log path

## Procedure

1. Confirm the candidate is inside `allowed_to_edit`.
2. Confirm no benchmark or previous-cycle artifact was modified.
3. Run the configured build command.
4. Capture stdout/stderr in `experiments/<cycle>/logs/compile.log`.
5. Confirm the expected binary exists.
6. Record warning count if warning policy is enabled.

## Pass Conditions

- build command exits 0
- expected binary exists
- no forbidden path was modified
- build log is stored under the active cycle

## Fail Conditions

- nonzero exit
- timeout
- missing binary
- build metadata drift outside scope
- warnings treated as errors by the cycle policy

## First-Cycle Note

For flow-only candidates, compile may be recorded as `SKIPPED` with the reason
`no source code changed`.

