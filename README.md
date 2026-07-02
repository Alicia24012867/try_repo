# Multi-Agent ABC Reproduction

Small-scale reproduction workspace for the paper "Autonomous Evolution of EDA
Tools: Multi-Agent Self-Evolved ABC".

## Layout

- `benchmarks/`: sampled benchmark suites, 10 designs per suite.
- `configs/`: agent prompts, rules, flow recipes, and evaluation settings.
- `docs/`: project documentation and paper reference files.
- `experiments/`: tracked experiment skeletons; generated logs and outputs are
  ignored.
- `scripts/`: automation entry points.
- `third_party/`: external source trees such as FlowTune, ignored by git.

## Local-Only Data

Use `.local/` for machine-specific files, scratch copies, temporary downloads,
large generated artifacts, and local run dumps. This directory is ignored.

See `docs/STRUCTURE.md` for the detailed mapping to the paper workflow.
