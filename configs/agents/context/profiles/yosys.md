# Yosys profile

Yosys provides a production example of wrapping ABC while protecting the wider
synthesis tool's frontend semantics and reporting failures clearly.

## High-value code index

- `passes/techmap/abc.cc`: default/fast ABC scripts, option handling, temporary
  interchange, subprocess or linked execution, cleanup, and diagnostics.
- `abc_new.cc`, `abc9_exe.cc`, `abc9.cc`: staged script execution and newer ABC
  integration boundaries.
- `aigmap.cc`: lowering logic to AIG-compatible primitives.
- `passes/opt/opt.cc`: orchestration of internal optimization passes.

## Transferable patterns

Keep a stable default flow, make custom scripts explicit, log the exact executed
recipe, fail closed on tool errors, and clean temporary state. Keep integration
and algorithm concerns separate. Script variants illustrate area/delay/runtime
trade-offs but include mapping and sequential commands that are outside this
Logic Agent's combinational scope.

## Caveats

Yosys RTLIL, passes, process management, and ABC interchange are not local ABC
APIs. Do not copy `dretime`, mapping/library operations, filesystem protocol, or
RTL semantics into the Logic patch. Use only orchestration and validation
discipline. License: ISC.
