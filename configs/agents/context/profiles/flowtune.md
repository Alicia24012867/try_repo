# FlowTune profile

This pinned checkout is both the paper-adjacent flow-tuning reference and the
actual local ABC fork used by candidate build/CEC/QoR gates. Local source always
wins over newer external examples.

## High-value code index

- `src/src/base/abci/abc.c`: the concrete command registry and wrapper behavior
  available in this fork. Its large size makes symbol-targeted excerpts vital.
- `abcRewrite.c`, `abcRefactor.c`, `abcResub.c`, `abcBalance.c`, `abcDar.c`:
  allowed Logic Agent wrappers and technology-independent entry points.
- `src/src/base/abc/abcBayestune.cpp`: FlowTune command integration, flow-string
  cleaning, evaluation invocation, and tuning-related orchestration precedent.
- `FlowTune-AIG-Optimization/`: experiment driver and flow examples. It is not
  a Logic Agent patch target.
- `src/src/base/abci/module.make`: current module build registration.

## Transferable patterns

Keep the single ABC binary/command model. Reuse command parsing and network
conversion style already present in the local wrapper. A candidate must be
reached by the assignment's evaluation flow, apply only in an isolated source
workspace, compile, then pass CEC before its size/depth/runtime rows are trusted.

## Caveats

This research repository has no top-level license file in the pinned checkout;
do not copy it into other projects. Some FlowTune drivers couple optimization,
subprocesses, and experiment files. Logic candidates must not introduce those
side effects into ABC algorithms or make benchmark-name-dependent decisions.
