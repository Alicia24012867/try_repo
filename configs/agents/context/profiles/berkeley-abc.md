# Berkeley ABC profile

Use this pinned upstream checkout as the authoritative architectural reference,
but compile candidates against the bundled FlowTune fork. The two revisions are
not API-identical.

## High-value code index

- `src/base/abci/abc.c`: command declarations, `Cmd_CommandAdd` registration,
  option parsing, network-form checks/conversions, and wrappers such as
  `Abc_CommandRewrite` and `Abc_CommandOrchestrate`.
- `src/base/abci/abcOrchestration.c`: upstream orchestration prototype and
  gain-aware rewrite/refactor/resub mechanisms. Treat experimental file I/O or
  dataset collection as research scaffolding, not production behavior to copy.
- `abcRewrite.c`, `abcRefactor.c`, `abcResub.c`, `abcBalance.c`, `abcDar.c`:
  wrapper-to-algorithm boundaries and network invariants.
- `src/opt/rwr`, `src/opt/res`, `src/opt/dar`: algorithm internals. These are
  read-only call-chain context unless the planner explicitly expands scope.
- `module.make`: how an in-tree source file is registered.

## Transferable patterns

Trace command registration through the wrapper and conversion path before
editing a decision. Preserve `Abc_NtkCheck`-style postconditions, allocation and
free ownership, deterministic defaults, and the distinction between logic and
strashed AIG networks. Prefer a bounded parameter/tie-break or an orchestration
of existing correctness-preserving passes over a new Boolean transformation.

## Caveats

Upstream contains features absent from FlowTune, including orchestration code.
Never paste an upstream call without verifying every type, symbol, option, and
module dependency locally. Known ABI drift includes extra parameters in current
`Abc_NtkRefactor` and `Abc_NtkResubstitute` compared with the pinned FlowTune
fork. Treat the FlowTune declarations/call sites as authoritative and migrate a
strategy, never a whole function. Do not copy research CSV/file-writing side effects.
License: the permissive UC notice in `copyright.txt` must be preserved if code
is ever adapted; prompt excerpts remain reference-only.
