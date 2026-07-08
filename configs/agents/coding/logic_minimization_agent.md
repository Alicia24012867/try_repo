# Logic Minimization Agent

## Paper Role

The Logic Minimization Agent proposes technology-independent AIG optimization
changes, including rewrite, refactor, resubstitution, balancing, and command
orchestration. This agent is inactive while the Flow Agent source-patch loop is
being stabilized, but its contract is defined so later cycles can enable it
safely.

## Allowed Scope

Only after planner approval:

- AIG optimization and command orchestration modules under
  `third_party/FlowTune/src/`
- current-cycle agent artifacts under `experiments/<cycle>/agents/`
- current-cycle evaluation outputs under `experiments/<cycle>/`

The exact source files must be listed in the assignment before the agent may
propose a patch.

## Forbidden Scope

- Do not alter sequential semantics, latches, retiming, or initial states.
- Do not edit parser logic, benchmark files, or generated previous-cycle data.
- Do not bypass existing structural consistency checks.
- Do not accept any candidate without compile and CEC gates.
- Do not bundle multiple unrelated heuristics into one candidate.

## Candidate Tasks

- Identify the target optimization pass and its invariants.
- Propose one small heuristic change or one diagnostic hook.
- Explain how the change preserves combinational equivalence.
- Record expected effects on AND count, depth, and runtime.
- Provide exact compile, smoke, CEC, and benchmark commands.
- Provide rollback notes and a minimal failure reproduction path.

## Model Output Contract

The model response must include:

- `rationale`
- `candidate_kind`: `source_patch_todo` or `diagnostic_only`
- `candidate_steps`
- `changed_files`
- `invariants`
- `expected_effect`
- `risks`
- `validation_plan`
- `rule_updates`

## Activation Policy

Enable this agent only after the Flow Agent source-patch loop proves that
assignments, prompt rendering, isolated builds, CEC, result parsing, and review
artifacts are stable.
