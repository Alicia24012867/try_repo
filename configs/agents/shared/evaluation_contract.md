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

For the first flow-only cycle, compile may be marked `SKIPPED` because no source
code is changed.

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
- Current caveat: `cycle_000` and the first small flow cycle may report QoR as
  provisional until this gate is wired into the harness.

## Benchmark Gate

- First small cycle: EPFL subset containing `epfl_adder`, `epfl_bar`, and
  `epfl_sqrt`.
- Full sampled scope: the 10-design subsets under `benchmarks/`.
- Every benchmark run must record design name, input path, flow path, log path,
  output path, exit status, runtime, AND count, depth, and skip reason.

## QoR Metrics

- Primary first-cycle metric: AIG AND count.
- Secondary first-cycle metrics: AIG depth, runtime, skipped design count,
  crash/assertion count.
- Later mapping metrics: LUT count, mapper area, mapper delay estimate.
- Later timing metrics: STA worst slack and post-buffer/sizing area when a
  timing flow is available.

## Acceptance Policy

- `ACCEPT_PROCESS`: candidate artifacts are complete and benchmarked, but
  correctness remains provisional.
- `ACCEPT_QOR`: compile/smoke/CEC pass and QoR improves within regression
  limits.
- `REQUEST_REPAIR`: failure is local and still inside allowed scope.
- `REJECT`: correctness failure, scope violation, unreproducible artifact, or
  benchmark-name hard-coding.
- `DEFER`: insufficient evidence or missing evaluation gate.

