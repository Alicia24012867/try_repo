# Source Patch Proposal -- cycle_001 candidate_001

## Status

- Candidate kind: `source_patch_todo`
- Materialization: proposal-only
- Source patch applied: no
- Proposed target source files written: no
- Next gate: S4 source patch application and compile validation

## Rationale

Prepare a reviewable Flow Agent source-patch proposal path without applying source edits in S1/S2.

## Expected Effect

Allows the Flow Agent to propose scoped source-level changes while preserving existing abc_flow behavior.

## Proposed Target Files

- scripts/agents/self_evolved_abc/flow/source_patch_runner.py

## Candidate Artifact Paths

- experiments/cycle_001/agents/source_patch_todos/candidate_001.md

## Candidate Steps

- Add proposal-only source patch contract handling for Flow Agent candidates
- Record target files, invariants, validation gates, and rollback guidance
- Keep source patch application disabled until a later materialization gate exists

## Entry Points

- scripts/agents/self_evolved_abc/flow/validation.py
- scripts/agents/self_evolved_abc/flow/materialization.py
- scripts/agents/self_evolved_abc/coding_agents/flow_agent.py

## Invariants

- abc_flow candidates still use ABC command validation
- source_patch_todo does not write or apply source edits in S1/S2
- All proposed files stay within assignment allowed_to_edit

## Risk Hotspots

- Accidentally applying a source patch before compile gates exist
- Weakening abc_flow validation while adding source-patch support

## Validation Plan

- python3 -B -m py_compile scripts/agents/self_evolved_abc/flow/validation.py scripts/agents/self_evolved_abc/flow/materialization.py scripts/agents/self_evolved_abc/coding_agents/flow_agent.py
- Run existing abc_flow fixtures and confirm behavior is unchanged
- Run source_patch_todo fixture validation and confirm no source file is written

## Risks

- The source patch proposal may still be too broad if S2 scope validation is bypassed

## Rollback Plan

Remove source_patch_todo from Flow candidate kinds and reject source patch proposal fixtures.

## Compatibility Notes

- command_interface: unchanged
- build_system: unchanged
- defaults: unchanged
- source_patch_application: disabled

## Evidence Files

- fixture

## Guardrails

- This artifact is not a patch file.
- Do not treat this proposal as applied implementation evidence.
- Do not compare QoR against this proposal until S4/S5 build and implementation comparison exist.
- Do not modify benchmarks, previous-cycle results, or `third_party/FlowTune/` unless a later assignment explicitly expands scope.
