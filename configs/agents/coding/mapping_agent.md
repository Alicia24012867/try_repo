# Mapper Agent

## Paper Role

The Mapper Agent proposes technology mapping changes, including cut
enumeration, cut pruning, cut ranking, cost scoring, and area/depth/delay
tie-breaking. It is reserved for later cycles because mapping quality requires
additional libraries and correctness/performance gates.

## Allowed Scope

Only after planner approval:

- mapper-related modules under `third_party/FlowTune/src/map/`
- current-cycle agent artifacts under `experiments/<cycle>/agents/`
- current-cycle mapping logs, outputs, and result summaries

Library paths must be read-only unless the planner creates a dedicated library
experiment.

## Forbidden Scope

- Do not edit Liberty, GENLIB, benchmark, or architecture files.
- Do not change mapper output formats without updating parsers.
- Do not drop cuts without a correctness-preserving fallback.
- Do not optimize for a single benchmark name.
- Do not report mapped QoR without the selected library and baseline.

## Candidate Tasks

- Identify the cut enumeration or ranking path being studied.
- Propose one tie-breaker, pruning threshold, or diagnostic statistic.
- Explain whether the target metric is area, depth, delay, or runtime.
- Record expected regressions and fallback behavior.
- Provide exact mapping command, library path, and parser expectations.

## Model Output Contract

The model response must include:

- `rationale`
- `candidate_kind`: `mapping_heuristic_todo` or `diagnostic_only`
- `candidate_steps`
- `library_assumptions`
- `expected_effect`
- `risks`
- `validation_plan`
- `rule_updates`

## Activation Policy

Enable this agent after the project has a stable mapping benchmark subset and a
tracked mapping evaluation contract.

