# mockturtle profile

mockturtle is a modern, generic C++ logic-network library. It is a design-pattern
reference, not an ABC-compatible implementation.

## High-value code index

- `algorithms/rewrite.hpp`: explicit `rewrite_params`/`rewrite_stats`, cut
  enumeration, cost decisions, callbacks, and optional zero-gain behavior.
- `refactoring.hpp`: reconvergence windows, resynthesis contracts, gain checks,
  and statistics.
- `resubstitution.hpp`, `aig_resub.hpp`, `sim_resub.hpp`: bounded divisors,
  inserted-node limits, simulation guidance, and acceptance accounting.
- `dont_cares.hpp`: care-set computation and the semantic cost of don't-care use.
- `cleanup.hpp`: removing dangling structure after legal substitutions.

## Transferable patterns

Separate tunable parameters from statistics; expose whether a heuristic is
actually reached; preserve an explicit gain/cost contract; bound window and
divisor growth; keep deterministic fallbacks; and make cleanup part of the
algorithm contract rather than an apparent QoR trick.

## Caveats

Templates, views, events, truth-table types, and ownership differ from ABC C.
Translate only the decision concept into locally available ABC fields. Never add
mockturtle as a dependency. CEC remains mandatory even when the reference API
itself promises an in-place semantics-preserving transform. License: MIT.
