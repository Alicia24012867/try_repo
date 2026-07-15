# OpenROAD-flow-scripts profile

ORFS is a reference for trustworthy, reproducible evaluation rather than a
source of ABC algorithm implementations.

## High-value code index

- `flow/scripts/abc_area.script`, `abc_speed.script`, and
  `abc_speed_gia_only.script`: objective-specific ABC recipes.
- `flow/scripts/yosys.tcl` and `synth.tcl`: synthesis-stage boundaries and
  configuration propagation.
- `flow/scripts/report_metrics.tcl` and `flow/util/genMetrics.py`: durable metric
  collection and reporting.
- `flow/scripts/README.md`: stage and script conventions.

## Transferable patterns

Pin inputs and tool revisions, keep evaluation recipes visible, record failures
and missing metrics, separate objective variants, and produce machine-readable
artifacts. These practices support the paper's reviewer feedback loop and help
prevent a candidate from hiding regressions through selective evaluation.

## Caveats

Physical-design PPA, libraries, and mapped netlists are not the current
technology-independent AIG objective. Do not import the ORFS harness, tune for
one design/platform, or use physical metrics as a substitute for CEC. Its build
and run scripts use the BSD-3-Clause notice in `LICENSE_BUILD_RUN_SCRIPTS`.
