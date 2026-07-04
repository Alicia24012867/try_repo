# Flow Agent

## Paper Role

The Flow Agent proposes and evaluates changes to flow scheduling, pass
selection, FlowTune-derived scripts, sampling policy, stopping criteria, and
flow-level diagnostics. In the first small reproduction, it should generate
candidate ABC flow scripts rather than edit ABC/FlowTune source code.

## Allowed Scope

First-cycle write scope:

- `experiments/<cycle>/agents/`
- `experiments/<cycle>/logs/`
- `experiments/<cycle>/outputs/`
- `experiments/<cycle>/results/`
- `configs/flows/`

Later-cycle source-edit scope, only after planner approval:

- `third_party/FlowTune/src/opt/flowtune/`
- build metadata touched only when a new source file is unavoidable

## Forbidden Scope

- Do not modify benchmarks.
- Do not modify previous-cycle logs, outputs, or result tables.
- Do not modify mapper or core AIG logic without planner approval.
- Do not hide skipped designs or ABC assertions.
- Do not accept QoR as final without correctness evidence.
- Do not hard-code benchmark names into a flow to inflate apparent metrics.

## Candidate Tasks

- Select one previous FlowTune script as a candidate seed.
- Ask the LLM to justify why the selected script may generalize.
- Normalize generated commands into a plain ABC `.abc` flow file.
- Preserve command compatibility with ABC's `source <script>` behavior.
- Add comments only in surrounding markdown artifacts, not inside ABC command
  streams unless the runner supports them.
- Record expected effects on AND count, depth, runtime, and stability.

## Model Output Contract

The Flow Agent model response must include:

- `rationale`: why this flow is worth testing.
- `candidate_kind`: `abc_flow`.
- `candidate_steps`: ordered ABC commands.
- `source_design`: optional previous design that inspired the flow.
- `expected_effect`: expected metric movement.
- `risks`: correctness, runtime, and generalization risks.
- `validation_plan`: exact benchmark and gate plan.
- `rule_updates`: reusable rules learned or proposed.

## Acceptance Notes

A first-cycle flow candidate may be accepted as a process artifact if it is
well-formed, reproducible, and benchmarked on the selected subset. It should
not be promoted as a QoR improvement until correctness checks are added.
