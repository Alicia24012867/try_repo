# Flow Agent

## Paper Role

The Flow Agent proposes and evaluates changes to flow scheduling, pass
selection, FlowTune-derived scripts, sampling policy, stopping criteria, and
flow-level diagnostics. The current reproduction exercises the source-level
feedback loop: the model proposes a scoped unified diff, the runner applies it
only inside an isolated workspace, the candidate ABC binary is built remotely,
and CEC-backed QoR feedback drives the next assignment.

## Allowed Scope

Active-cycle artifact scope:

- `experiments/<cycle>/agents/`
- `experiments/<cycle>/logs/`
- `experiments/<cycle>/outputs/`
- `experiments/<cycle>/results/`
- `experiments/<cycle>/impl_compare/`
- `configs/flows/`

Current source-patch scope:

- `third_party/FlowTune/src/src/opt/`
- build metadata touched only when a new source file is unavoidable

The assignment layer normalizes these paths through
`scripts/agents/self_evolved_abc/flow/assignment.py`.

## Forbidden Scope

- Do not modify benchmarks.
- Do not modify previous-cycle logs, outputs, or result tables.
- Do not modify mapper or core AIG logic without planner approval.
- Do not hide skipped designs or ABC assertions.
- Do not accept QoR as final without correctness evidence.
- Do not hard-code benchmark names into a flow to inflate apparent metrics.

## Candidate Tasks

- Read previous FlowTune evidence and the provided source-file context.
- Propose one minimal `source_patch_diff` that targets real files under the
  assignment's source-patch scope.
- Preserve the existing ABC command interface and default behavior unless the
  assignment explicitly authorizes a change.
- Identify invariants, rollback action, and exact remote validation gates.
- Keep generated `.abc` flow recipes available only as legacy or fixture
  artifacts unless `source_patch_mode: abc_flow` is selected.
- Add comments only in surrounding markdown artifacts, not inside ABC command
  streams unless the runner supports them.
- Record expected effects on AND count, depth, runtime, and stability.

## Source Patch Diff Checklist

Before returning a candidate in `source_patch_diff` mode, verify every item:

- The target file exists under `third_party/FlowTune/src/src/opt/` and appears
  in the source context given to the model.
- The patch changes one narrow behavior: a flow scheduling choice, sampling
  condition, stopping condition, conservative tie-break, or feedback log hook.
- The patch uses existing ABC/FlowTune style for naming, allocation, cleanup,
  assertions, error handling, and print/debug helpers.
- The patch does not add a new source file or build-system edit unless the
  assignment explicitly authorizes it.
- The patch does not modify benchmark files, previous-cycle artifacts, result
  parsers, CEC commands, QoR thresholds, or generated outputs.
- The unified diff uses repository-relative paths in all headers:
  `diff --git a/<path> b/<path>`, `--- a/<path>`, and `+++ b/<path>`.
- Every diff target is listed in `files_to_write`.
- The diff hunks contain real surrounding context and do not invent functions,
  variables, include names, or nonexistent paths.
- `validation_plan` separates local checks from remote checks. Local work may
  validate JSON, patch application, and Python smoke; remote work builds ABC,
  runs CEC, and collects QoR.

## Feedback-Driven Iteration

The Flow Agent should treat feedback as the next assignment, not as background
noise:

- Validation failure: repair JSON shape, mode selection, missing fields, and
  path scope before changing the optimization idea.
- Patch-apply failure: repair diff headers or hunk context against the real
  source file.
- Smoke failure: repair the smallest runner/command issue without weakening the
  smoke check.
- Compile failure: repair C syntax, includes, declarations, build metadata, or
  local helper usage.
- CEC failure: invalidate QoR, identify the semantic risk, and prefer rollback
  or a much narrower semantic repair.
- QoR regression: keep CEC-passing behavior, then reduce or redirect the
  heuristic. Do not add benchmark-name branches to rescue the average.
- Missing remote evidence: keep the candidate provisional and request the exact
  remote gate, rather than claiming acceptance.

## Model Output Contract

The Flow Agent model response must include:

- `rationale`: why this flow is worth testing.
- `candidate_kind`: matches assignment `source_patch_mode`; currently
  `source_patch_diff`.
- `candidate_steps`: ordered patch-plan steps, not shell commands.
- `source_patch`: unified diff payload for `source_patch_diff`.
- `source_design`: optional previous design that inspired the flow.
- `expected_effect`: expected metric movement.
- `files_to_write`: every patched source file plus active-cycle artifacts.
- `risks`: correctness, runtime, and generalization risks.
- `validation_plan`: exact benchmark and gate plan.
- `rule_updates`: reusable rules learned or proposed.

## Acceptance Notes

A source patch may be promoted only after isolated application, candidate binary
build, full CEC pass, and correctness-backed QoR improvement. Local macOS checks
are limited to prompt/schema/fixture validation; ABC execution and compilation
run on the remote Linux host.
