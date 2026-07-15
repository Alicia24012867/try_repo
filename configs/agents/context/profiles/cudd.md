# CUDD profile

Pinned source: `ivmai/cudd` commit
`f54f533303640afd5dbe47a05ebeabb3066f2a25` (default branch `release`).
CUDD is the established C package for BDD, ADD, and ZDD manipulation.  This
GitHub tree is a maintained fork/mirror of the CU Decision Diagram package and
is useful for Boolean-function algorithms and resource-discipline patterns,
not as a replacement for ABC's native decision-diagram or AIG code.

## High-value code index

- `cudd/cudd.h`: public manager, node, reference-count, reordering, and Boolean
  operation contracts.
- `cudd/cuddCof.c` and `cudd/cuddCompose.c`: cofactor, constrain/restrict, and
  functional composition implementations.
- `cudd/cuddDecomp.c`: bounded decomposition families and their failure paths.
- `cudd/cuddReorder.c`: explicit dynamic-reordering policy and manager state.
- `cudd/cuddSat.c`: satisfying-cube and shortest-path reasoning over a shared
  decision diagram.
- `cudd/cuddRef.c` and `cudd/cuddCheck.c`: reference ownership and structural
  consistency checks.

## Quality and self-evolution value

CUDD is mature C with stable public contracts, extensive internal assertions,
memoization, and explicit node ownership.  It offers a strong prior for
cofactor-based reasoning, decomposition feasibility, cache-aware recursion,
bounded resource handling, and fail-closed cleanup.  These are especially
useful when a Logic Agent proposes a local Boolean heuristic whose intermediate
state must remain deterministic and leak-free.

## Transferable patterns

Separate manager policy from one operation; cache only canonical inputs;
balance recursive progress with explicit terminal cases; check allocation
failures; and pair every acquired reference with the correct dereference path.
Treat dynamic reordering as observable global state rather than an invisible
optimization.

## Caveats

CUDD nodes are canonical decision-diagram nodes managed by unique tables;
ABC AIG nodes have different complemented-edge, fanout, traversal-ID, and
memory conventions.  `Cudd_Ref`/`Cudd_RecursiveDeref`, caches, and reordering
APIs must never be transplanted into ABC.  Do not add CUDD as a candidate
dependency; translate only bounded decision concepts and verify the resulting
local ABC implementation with CEC.  License: BSD-3-Clause.
