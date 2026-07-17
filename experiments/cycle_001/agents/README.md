# Cycle 001 Paired Candidate Workspace

This is the first frozen dual-agent dispatch.

- `assignments/flow_candidate_001.json`: Flow Agent lane; owns `src/opt`.
- `assignments/logic_candidate_001.json`: Logic Minimization Agent lane; owns
  `src/base/abci`.
- `plans/`, `candidate_changes/`, `source_patches/`, `feedback/`, and
  `rule_updates/` are keyed by candidate id and never shared as mutable source.
- `attempts/<candidate_id>/` is created by the retry runner for typed model,
  validation, and compile-repair evidence.

The shared frozen dispatch and final fan-in live in `../planning/`; implementation
outputs are isolated below `../candidates/<candidate_id>/impl_compare/`.
