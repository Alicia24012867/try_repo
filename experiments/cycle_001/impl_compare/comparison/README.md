# Implementation Compare Setup -- cycle_001 candidate_001

## Status

- Stage: S4c build/smoke passed
- Source patch applied: true
- Build run: true
- Benchmark/QoR run: no
- Promotion allowed: no

## Baseline Implementation

- Label: `baseline_unmodified`
- Binary: `third_party/FlowTune/FlowTune-OpenFPGA/abc`
- Binary exists: `true`
- Build status: `baseline_smoke_passed`
- Build command: `python_smoke_gate`
- Build exit code: `0`
- Build info: `../baseline_unmodified/build_info.json`

## Candidate Implementation

- Label: `candidate_modified`
- Binary: `third_party/FlowTune/FlowTune-OpenFPGA/abc`
- Binary exists: `true`
- Patch applied: `true`
- Patch diff: `experiments/cycle_001/impl_compare/candidate_modified/patch.diff`
- Build status: `build_smoke_passed`
- Build command: `python_smoke_gate`
- Build exit code: `0`
- Build info: `../candidate_modified/build_info.json`

## Patch Plan

- Path: `experiments/cycle_001/agents/source_patch_todos/candidate_001.md`
- Exists: `true`
- SHA256: `1085b0fdf2fc72f95a9ecb216b01a13451cc35a27749b32cbc6f42fab5164d52`

## Next Gate

- S5 can start implementation comparison with the same benchmark harness.
- Promotion stays blocked until S5/F7 pass the CEC-first QoR gate.
