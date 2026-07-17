# Cycle 001 Evaluation Portfolio

`cycle_001` is a frozen paired Flow/Logic dispatch, not the former single
`candidate_001` flow experiment. The authoritative assignments are:

- `experiments/cycle_001/agents/assignments/flow_candidate_001.json`
- `experiments/cycle_001/agents/assignments/logic_candidate_001.json`

Both assignments evaluate `large_70`: 30 ABC-native designs and 40 Verilog
designs. Verilog is lowered once with Yosys to a candidate-scoped BLIF under
`candidates/<candidate>/impl_compare/frontend/`; the exact normalized input is
then shared by the baseline and candidate binary.

## Frozen Flow Portfolio

1. `candidate_recipe`: candidate-specific recipe materialized from the frozen
   assignment's `evaluation_flow_commands`.
2. `rewrite_refactor`: `strash; rewrite -z; refactor -z; rewrite -z; resub -K
   8; strash; print_stats`.
3. `resub_dc2`: `strash; resub -K 8; dc2; refactor -z; resub -K 8; strash;
   print_stats`.

## Decision Artifacts

For each candidate lane, S5/F7 writes:

- `comparison/frontend_summary.csv`: source input and Yosys result.
- `comparison/cec_by_flow.csv` and `comparison/qor_delta_by_flow.csv`:
  immutable detailed evidence for every design/flow pair.
- `comparison/cec_summary.csv`: one strict all-flow CEC result per design.
- `comparison/flow_vote_summary.csv`: strict-majority per-design vote.
- `comparison/qor_delta.csv`: one median aggregate QoR row per design; this is
  the only vector consumed by the promotion review.
- `comparison/multi_flow_summary.json`: per-flow scoreboard and aggregate
  counters.

CEC must pass in every flow. Median aggregation and voting make QoR comparison
robust to a single flow's noise, but the default all-flow AND non-regression
guard means a vote can never promote an unsafe candidate.

Run the candidate pipeline on the Linux/ABC host through `bash run.sh`; do not
manually use the old root-level `experiments/cycle_001/outputs/` paths.
