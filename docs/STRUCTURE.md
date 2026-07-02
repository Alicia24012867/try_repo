# Repository Structure

This layout mirrors the organization described in the paper at a small
reproduction scale. The paper does not prescribe an exact public directory tree,
so this repository maps its main concepts into local experiment folders.

## Paper Concept Mapping

- `third_party/FlowTune/`: upstream ABC/FlowTune codebase used as the baseline
  integration substrate.
- `benchmarks/`: benchmark suites used by the evaluation loop.
- `configs/agents/`: notes or rules for the paper's agent roles, such as flow
  tuning, logic minimization, and mapping.
- `configs/flows/`: ABC command recipes for baseline, FlowTune, CEC, and later
  mapping or STA flows.
- `configs/evaluation/`: metric definitions, run settings, and aggregation
  conventions.
- `experiments/cycle_000/`: initial non-evolved baseline cycle, corresponding
  to the paper's cycle 0 / pre-evolution evaluation stage.
- `scripts/`: local and remote automation for running benchmarks and summarizing
  results.

## Benchmark Suites

The paper evaluates ISCAS, ITC, EPFL, VTR, and arithmetic blocks. This small
reproduction keeps a 10-design sample for each local suite:

- `benchmarks/epfl/`
- `benchmarks/iscas85/`
- `benchmarks/iscas89/`
- `benchmarks/iscas99/`
- `benchmarks/itc99/`
- `benchmarks/vtr/`
- `benchmarks/arithmetic/`

See `benchmarks/SOURCES.md` for the exact source and sampling notes.

## Experiment Cycles

Each experiment cycle keeps generated artifacts together:

- `logs/`: raw ABC, FlowTune, compile, and CEC logs.
- `outputs/`: generated AIG/BLIF/netlist files.
- `results/`: parsed CSV/JSON summaries and final tables.

For the 12-hour reproduction, use `experiments/cycle_000/` as the main run
directory and start with EPFL plus one or two smaller suites before expanding to
all sampled benchmarks.
