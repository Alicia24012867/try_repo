# Flow Evaluation Plan -- cycle_001 candidate_001

## Scope

- Agent: flow_agent
- Candidate flow: `configs/flows/cycle_001_candidate_001.abc`
- Benchmark count: 3
- ABC command root: run from the repository root on the execution host.
- Correctness status before CEC: `provisional_no_cec`.

## Result CSV Schema

```text
benchmark,flow_label,abc_exit_code,aig_nodes,aig_depth,runtime_seconds,skipped_reason,correctness_status
```

## Commands

### benchmarks/epfl/epfl_adder.blif

- Baseline label: `vanilla_strash`
- Candidate label: `candidate_flow`
- Baseline log: `experiments/cycle_001/logs/epfl_adder.vanilla_strash.log`
- Candidate log: `experiments/cycle_001/logs/epfl_adder.candidate_flow.log`

Baseline:

```bash
abc -c "source third_party/FlowTune/abc.rc; read benchmarks/epfl/epfl_adder.blif; strash; write_aiger experiments/cycle_001/outputs/epfl_adder.vanilla_strash.aig; ps"
```

Candidate:

```bash
abc -c "source third_party/FlowTune/abc.rc; read benchmarks/epfl/epfl_adder.blif; source configs/flows/cycle_001_candidate_001.abc; strash; write_aiger experiments/cycle_001/outputs/epfl_adder.candidate_flow.aig; ps"
```

CEC note:

- CEC is not automated in F5. The commands above write `experiments/cycle_001/outputs/epfl_adder.vanilla_strash.aig` and `experiments/cycle_001/outputs/epfl_adder.candidate_flow.aig`. Check equivalence with `abc -c "cec experiments/cycle_001/outputs/epfl_adder.vanilla_strash.aig experiments/cycle_001/outputs/epfl_adder.candidate_flow.aig"`. Until then, set correctness_status to `provisional_no_cec`.

### benchmarks/epfl/epfl_bar.blif

- Baseline label: `vanilla_strash`
- Candidate label: `candidate_flow`
- Baseline log: `experiments/cycle_001/logs/epfl_bar.vanilla_strash.log`
- Candidate log: `experiments/cycle_001/logs/epfl_bar.candidate_flow.log`

Baseline:

```bash
abc -c "source third_party/FlowTune/abc.rc; read benchmarks/epfl/epfl_bar.blif; strash; write_aiger experiments/cycle_001/outputs/epfl_bar.vanilla_strash.aig; ps"
```

Candidate:

```bash
abc -c "source third_party/FlowTune/abc.rc; read benchmarks/epfl/epfl_bar.blif; source configs/flows/cycle_001_candidate_001.abc; strash; write_aiger experiments/cycle_001/outputs/epfl_bar.candidate_flow.aig; ps"
```

CEC note:

- CEC is not automated in F5. The commands above write `experiments/cycle_001/outputs/epfl_bar.vanilla_strash.aig` and `experiments/cycle_001/outputs/epfl_bar.candidate_flow.aig`. Check equivalence with `abc -c "cec experiments/cycle_001/outputs/epfl_bar.vanilla_strash.aig experiments/cycle_001/outputs/epfl_bar.candidate_flow.aig"`. Until then, set correctness_status to `provisional_no_cec`.

### benchmarks/epfl/epfl_sqrt.blif

- Baseline label: `vanilla_strash`
- Candidate label: `candidate_flow`
- Baseline log: `experiments/cycle_001/logs/epfl_sqrt.vanilla_strash.log`
- Candidate log: `experiments/cycle_001/logs/epfl_sqrt.candidate_flow.log`

Baseline:

```bash
abc -c "source third_party/FlowTune/abc.rc; read benchmarks/epfl/epfl_sqrt.blif; strash; write_aiger experiments/cycle_001/outputs/epfl_sqrt.vanilla_strash.aig; ps"
```

Candidate:

```bash
abc -c "source third_party/FlowTune/abc.rc; read benchmarks/epfl/epfl_sqrt.blif; source configs/flows/cycle_001_candidate_001.abc; strash; write_aiger experiments/cycle_001/outputs/epfl_sqrt.candidate_flow.aig; ps"
```

CEC note:

- CEC is not automated in F5. The commands above write `experiments/cycle_001/outputs/epfl_sqrt.vanilla_strash.aig` and `experiments/cycle_001/outputs/epfl_sqrt.candidate_flow.aig`. Check equivalence with `abc -c "cec experiments/cycle_001/outputs/epfl_sqrt.vanilla_strash.aig experiments/cycle_001/outputs/epfl_sqrt.candidate_flow.aig"`. Until then, set correctness_status to `provisional_no_cec`.

## Runner Responsibilities

- Capture stdout and stderr for every command under `experiments/cycle_001/logs/`.
- Record wall-clock runtime outside ABC and store it as `runtime_seconds`.
- Parse `ps` output into `aig_nodes` and `aig_depth`.
- Preserve AIG outputs under `experiments/cycle_001/outputs/` for later CEC.
- Write one row per benchmark and flow label using the CSV schema above.
- Do not promote QoR while `correctness_status` is `provisional_no_cec`.
