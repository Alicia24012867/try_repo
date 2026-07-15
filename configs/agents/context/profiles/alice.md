# alice profile

Pinned source: `lsils/alice` commit
`8881c2c0a7282d2722de800039e276d9c298c255` (default branch `master`).
alice is the C++14 command-shell layer used across the EPFL logic-synthesis
ecosystem.  It is an orchestration and interface-design reference, not a
source of Boolean optimization algorithms.

## High-value code index

- `include/alice/command.hpp`: command lifecycle, option registration,
  validity checks, execution, and structured command logging.
- `include/alice/cli.hpp`: command discovery, parsing, execution boundaries,
  aliases, and error-facing shell behavior.
- `include/alice/store.hpp` and `include/alice/store_api.hpp`: typed current
  state, history, and mutation boundaries between commands.
- `include/alice/detail/logging.hpp`: durable execution metadata and JSON-facing
  logging conventions.
- `include/alice/alice.hpp` and `include/alice/api.hpp`: explicit extension
  points used to register commands and data types.

## Quality and self-evolution value

alice is compact, documented, and separates command registration, state, and
logging cleanly.  For Planning and Flow agents it provides useful precedents
for discoverable pass interfaces, validation before execution, typed state
handoff, command histories, and machine-readable observations.  Those patterns
support auditable candidate loops without coupling algorithms to the runner.

## Transferable patterns

Keep parsing and validation outside the transform; expose commands through one
registry; make inputs, current state, and produced state explicit; and log the
exact invocation before interpreting results.  A failed precondition should
stop a command without partially updating shared state.

## Caveats

alice's macros, C++ templates, Python bindings, CLI parser, and store model do
not match ABC's `Cmd_CommandAdd`, frame, network, or `Abc_Obj_t` APIs.  Do not
introduce alice as a dependency or build a second shell inside ABC.  Adapt only
the separation-of-concerns and observability patterns to the local command
wrappers and dual-agent workflow.  License: MIT.
