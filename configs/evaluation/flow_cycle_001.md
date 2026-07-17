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

The default portfolio is the eight standard ABC technology-independent flows:

1. `resyn`
2. `resyn2`
3. `resyn2a`
4. `resyn3`
5. `compress`
6. `compress2`
7. `resyn2rs`
8. `compress2rs`

`multi_flow.py` stores the expanded command sequence for each alias in the
frozen assignment. The runner applies a common `strash` setup before every
flow, so the commands always run on the same AIG representation. A custom
assignment may still opt into `candidate_recipe`, but it is not part of the
default eight-flow portfolio.

## FlowTune MAB Scheduler Lane

In addition to the eight static Table recipes, the frozen contract contains
`ftune_mab_aig_nodes`. It invokes the `ftune` command registered by
`abcBayestune.cpp` with the bounded AIG-node budget
`-r 1 -t 0 -p 1 -i 1 -s 1`. `ftune` selects a recipe in an isolated sandbox;
the evaluator allowlists and replays that selected recipe using the same
baseline or candidate binary, then subjects its output to the ordinary CEC and
aggregation gates. The MAB lane is supplemental and is not presented as a
ninth paper Table recipe.

## ASAP7 Physical QoR Lane

For every per-flow AIG that has passed CEC, the evaluator loads the bundled
`third_party/FlowTune/7nm_lvt_ff.lib` (`ASAP7_7nm_LVT_FF`) and replays the
FlowTune STA-demo sequence:

```text
read_lib; map; topo; upsize; dnsize; topo; stime
```

It records post-sizing cell area and STA critical-path delay in
`comparison/asap7_qor_by_flow.csv`, together with normalized area, delay, and
area-delay-product ratios in `comparison/asap7_qor_summary.json`. The frozen
assignment sets `asap7_qor.clock_period_ps: 1000.0`, yielding WNS on a uniform
1 GHz reference basis. The paper does not disclose a numeric SDC, so this is a
project-reference WNS comparison rather than a claim that absolute paper WNS
has been reproduced. Any override must freeze both `clock_period_ps` and its
`clock_period_source` in the contract.

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
