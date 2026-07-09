# Evaluation Contract

This contract defines how a candidate is evaluated after an agent proposes it.
The prompt layer may recommend commands, but the harness is responsible for
running and recording them.

## Compile Gate

- Command: use the project's existing ABC/FlowTune build command for the target
  environment.
- Required artifact: an ABC binary or equivalent executable path.
- Log path: `experiments/<cycle>/logs/compile.log`.
- Timeout: set by the cycle assignment.
- Pass condition: command exits with status 0 and produces the expected binary.
- Fail condition: nonzero exit, missing binary, build-system error, or timeout.

For legacy `abc_flow` candidates, compile may be marked `SKIPPED` because no
source code is changed. For the current `source_patch_diff` loop, candidate
binary build is required before implementation comparison.

## Smoke Gate

- Command: run the ABC binary with a minimal `read; strash; ps` flow on one
  small benchmark.
- Log path: `experiments/<cycle>/logs/smoke.log`.
- Pass condition: ABC exits 0 and prints parseable `ps` statistics.
- Fail condition: crash, assertion, missing statistics, or missing output.

## Correctness Gate

- Preferred gate: ABC CEC comparing baseline and candidate outputs for each
  measured design.
- Log path: `experiments/<cycle>/logs/<design>.cec.log`.
- Pass condition: every measured design is equivalent.
- Fail condition: CEC mismatch, timeout, crash, or missing comparison output.
- Current caveat: `cycle_000` is baseline evidence without independent CEC.
  Later source-patch cycles must use S5/F7 CEC-backed `qor_delta.csv` rows
  before QoR is considered reviewable.

## Benchmark Gate

- Fast smoke suite: `epfl_10`.
- Standard suite: `standard_30` (EPFL + ISCAS85 + ISCAS89 BLIF designs).
- Large suite: `large_70` (all seven 10-design sampled suites under
  `benchmarks/`, including Verilog designs).
- Current frontend: `abc_native`. S5/F7 invokes ABC directly, so the current
  promotion gate evaluates only ABC-native inputs (`.blif`, `.bench`, `.aig`).
  Verilog entries from `large_70` remain in `benchmark_scope` for paper-family
  coverage tracking but are listed in `unsupported_benchmark_scope` until a
  Verilog/Yosys frontend is connected.
- Every benchmark run must record design name, input path, flow path, log path,
  output path, exit status, runtime, AND count, depth, and skip reason.

## QoR Metrics

- Primary source-patch metric: AIG AND count.
- Secondary source-patch metrics: AIG depth, runtime, skipped design count,
  crash/assertion count.
- Later mapping metrics: LUT count, mapper area, mapper delay estimate.
- Later timing metrics: STA worst slack and post-buffer/sizing area when a
  timing flow is available.

## Acceptance Policy

- `ACCEPT_FOR_NEXT_CYCLE`: candidate build/smoke/CEC pass and correctness-backed
  QoR improves on the target metric. When no previous champion is recorded, a
  full-CEC, positive-total, no-regression evaluated candidate may bootstrap the
  first champion; later candidates must beat the current champion under the
  configured promotion thresholds.
- `REPAIR_VALIDATION`: model JSON, mode, or path scope validation failed.
- `REPAIR_PATCH`: unified diff did not apply in the isolated workspace.
- `REPAIR_SMOKE` or `REPAIR_COMPILE`: local Python smoke or candidate C build
  failed.
- `REJECT_CEC`: CEC failed, skipped, timed out, crashed, or was unparseable
  inside the evaluated benchmark scope.
- `REPAIR_QOR`: CEC passed but target QoR did not improve.
- `DEFER`: insufficient evidence or missing evaluation gate.
