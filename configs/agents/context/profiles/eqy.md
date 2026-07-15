# EQY profile

Pinned source: `YosysHQ/eqy` commit
`fe7163db01bc332941cefa11d6a62d57cde792c7` (default branch `main`).
EQY is YosysHQ's front-end driver for formal hardware equivalence checking.
It is a validation-orchestration reference for the reviewer and planning loop;
ABC's own CEC command remains authoritative for this project's candidate gate.

## High-value code index

- `src/eqy.py`: configuration parsing and top-level prepare/partition/prove
  phase orchestration.
- `src/eqy_job.py`: subprocess jobs, dependency scheduling, status propagation,
  cancellation, logs, and final PASS/FAIL handling.
- `src/eqy_partition.cc`: structural partition construction, merge/amend rules,
  emitted metadata, and database integrity checks.
- `src/eqy_combine.cc`: gold/gate combination and correspondence boundaries.
- `docs/source/strategies.rst` and `docs/source/config.rst`: ordered proof
  strategies, fallbacks, timeouts, and explicit configuration semantics.
- `examples/simple/` and `examples/nerv/`: passing and intentionally failing
  equivalence configurations.

## Quality and self-evolution value

EQY is an actively maintained YosysHQ project with visible phase boundaries,
parallel proof jobs, durable artifacts, and unambiguous terminal statuses.  It
provides high-value precedents for the Planning Agent's fan-out/fan-in loop:
both branches must settle, a proof failure cannot become a QoR winner, and
timeouts or incomplete partitions must remain distinct from PASS.

## Transferable patterns

Pin gold and gate inputs; separate preparation from proof; retain per-partition
status and logs; run independent strategies concurrently; cancel only when the
configured proof policy permits it; and aggregate to PASS solely from complete
successful evidence.  Preserve counterexample/failure artifacts for the next
planning cycle instead of collapsing them into one scalar reward.

## Caveats

EQY reasons about Yosys RTLIL/netlists and invokes external formal engines; it
does not validate an in-memory ABC AIG candidate or replace the project's
baseline-vs-candidate CEC recipe.  Do not import its Python runner, Yosys passes,
partition assumptions, or solver dependencies.  Reuse only lifecycle,
provenance, and all-settled review patterns.  License: ISC (`COPYING`).
