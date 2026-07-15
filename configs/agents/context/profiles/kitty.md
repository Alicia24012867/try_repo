# kitty profile

Pinned source: `lsils/kitty` commit
`61ec6bd2a1970596651155f8f68b8f4b6487a58b` (default branch `master`).
kitty is a focused C++17 truth-table library from the EPFL logic-synthesis
ecosystem.  Its small, typed algorithms make it a high-quality reference for
the Boolean functions manipulated inside cuts, but it is not an ABC network
implementation.

## High-value code index

- `include/kitty/operations.hpp`: cofactors, support minimization, variable
  transforms, composition, and care-aware truth-table operations.
- `include/kitty/decomposition.hpp`: Boolean decomposition predicates and
  constructive decomposition helpers.
- `include/kitty/isop.hpp` and `include/kitty/esop.hpp`: recursive cover
  construction and cube accounting.
- `include/kitty/npn.hpp`: exact and heuristic NPN canonization with explicit
  phase/permutation results.
- `include/kitty/dynamic_truth_table.hpp` and
  `include/kitty/partial_truth_table.hpp`: representation and size contracts
  behind bounded function reasoning.

## Quality and self-evolution value

The library has a narrow responsibility, tests, documentation, and explicit
type/size preconditions.  It helps a Logic Agent reason about cut functions,
care sets, canonical signatures, and decomposition opportunities before
changing an AIG.  Deterministic canonical forms are also useful precedents for
stable cache keys and reproducible heuristic tie-breaks.

## Transferable patterns

Keep truth-table width bounded; make support and phase/permutation mappings
explicit; distinguish the function from its don't-care set; and verify a
decomposition by recomposition.  Use canonization to share analysis, not as
proof that a replacement is legal in the surrounding network.

## Caveats

kitty templates, word layout, variable order, and ownership are unrelated to
ABC's `word` truth tables, cuts, complemented edges, and allocation APIs.  Do
not add kitty as a dependency or paste its C++ into the FlowTune C fork.
Translate only the mathematical idea through locally available ABC utilities,
then retain compile, reachability, CEC, and QoR gates.  License: MIT.
