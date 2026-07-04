# QoR Review

Use this checklist after compile, smoke, and correctness gates have been
accounted for.

## Required Inputs

- baseline summary table
- candidate summary table
- skipped or failed design table
- benchmark scope
- primary metric
- secondary metrics
- normalization baseline

## Review Procedure

1. Confirm benchmark coverage matches the assignment.
2. Exclude skipped designs from improvement averages, but report them.
3. Compare primary metric per design.
4. Compare secondary metrics per design.
5. Compute weighted improvement across all complete designs.
6. Compute mean per-design improvement.
7. Flag any depth or runtime regression.
8. Confirm correctness status before promotion.

## First-Cycle Metrics

- primary metric: AIG AND count
- secondary metrics: AIG depth, runtime, skipped count, assertion count
- normalization: previous-cycle baseline on the same benchmark subset

## Acceptance Thresholds

- Process acceptance: complete artifacts and parseable results.
- QoR acceptance: positive weighted primary-metric improvement with no
  correctness failure.
- Repair request: local failure that can be addressed within current scope.
- Rejection: correctness failure, broad regression, missing artifact, or scope
  violation.

