# CEC Check

Use this checklist to establish functional equivalence before accepting QoR as
final.

## Required Inputs

- original benchmark path
- baseline output path
- candidate output path
- ABC binary path
- CEC command
- timeout
- log path

## Procedure

1. Confirm baseline and candidate outputs exist.
2. Run the configured CEC command for each measured design.
3. Store logs as `experiments/<cycle>/logs/<design>.cec.log`.
4. Parse pass/fail patterns from the log.
5. Record timeouts, crashes, and missing outputs separately.

## Pass Conditions

- every measured design is equivalent
- no CEC command times out
- every CEC log is present and parseable

## Fail Conditions

- equivalence mismatch
- ABC crash or assertion
- timeout
- missing baseline or candidate output
- unparseable CEC result

## Current Small-Reproduction Caveat

CEC is not yet wired into `cycle_000`. Until it is added, QoR summaries are
diagnostic evidence rather than final correctness-backed claims.

