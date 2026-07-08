# Self-Evolved Rulebase

The rulebase records constraints and lessons learned across cycles. Rules are
evidence-backed; a rule should cite a cycle artifact before it is promoted from
proposal to active policy.

## Global Rules

- R-GLOBAL-001: Preserve functional equivalence. QoR improvements are
  provisional until CEC or an equivalent correctness gate passes.
- R-GLOBAL-002: Compile and smoke checks must pass before benchmark evaluation.
- R-GLOBAL-003: Agents may edit only paths listed in `allowed_to_edit`.
- R-GLOBAL-004: Previous-cycle logs, outputs, and result tables are read-only.
- R-GLOBAL-005: Every skipped, timed-out, crashed, or assertion-failing design
  must be listed with a reason.
- R-GLOBAL-006: A candidate must test one primary hypothesis.
- R-GLOBAL-007: Do not hard-code benchmark names into an optimization or flow.
- R-GLOBAL-008: Store every plan, candidate note, feedback record, and rule
  update under the active cycle's `agents/` directory.

## Flow Agent Rules

- R-FLOW-001: In `source_patch_diff` mode, Flow Agent candidates must produce a
  unified diff targeting assignment-approved FlowTune source paths.
- R-FLOW-002: The assignment `source_patch_mode` and model `candidate_kind` must
  match for materialized proposals.
- R-FLOW-003: Source patches must apply only inside the isolated candidate
  workspace before any build, CEC, or QoR comparison.
- R-FLOW-004: Legacy `abc_flow` candidates must contain valid ABC commands and
  remain executable via `source <flow_file>`.
- R-FLOW-005: Do not select evidence from skipped designs as positive guidance.
- R-FLOW-006: Record the source design, source file entry points, invariants,
  and rollback action for every generated Flow Agent candidate.

## Logic Minimization Agent Rules

- R-LOGIC-001: Keep technology-independent candidates combinational unless the
  planner explicitly enables sequential experiments.
- R-LOGIC-002: Do not change parser behavior or benchmark input semantics.
- R-LOGIC-003: Any source patch must identify invariants and rollback criteria.
- R-LOGIC-004: AIG node and depth changes must be reported per benchmark.

## Mapping Agent Rules

- R-MAP-001: Do not edit Liberty, GENLIB, architecture, or benchmark files.
- R-MAP-002: Report the library and mapping command used for every result.
- R-MAP-003: Do not prune cuts without a fallback or correctness argument.
- R-MAP-004: Separate area, depth, delay, and runtime objectives.

## Rule Evolution

- Add a rule when a cycle reveals a reusable constraint or failure mode.
- Tighten a rule when a candidate passes locally but fails to generalize.
- Relax a rule only after at least one successful cycle shows the restriction is
  unnecessarily blocking progress.
- Retire a rule only in a review artifact that cites the replacement policy.
