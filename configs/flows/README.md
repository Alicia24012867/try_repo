# Generated Candidate Recipes

The paired workflow materializes one deterministic recipe per candidate at
runtime:

- `cycle_<NNN>_flow_candidate_001.abc`
- `cycle_<NNN>_logic_candidate_001.abc`

`evaluation_flow_commands` materializes the candidate-local diagnostic recipe.
The default promotion portfolio freezes eight expanded standard ABC recipes in
`evaluation_flows`: `resyn`, `resyn2`, `resyn2a`, `resyn3`, `compress`,
`compress2`, `resyn2rs`, and `compress2rs`, plus the supplemental
`ftune_mab_aig_nodes` scheduler lane. Static flows are rendered directly by
`implementation_compare.py`; the MAB lane executes `ftune`, validates its
emitted script, and replays it with the evaluated binary. A custom assignment
must explicitly request `candidate_recipe` for that generated file to become
an evaluation flow.

Do not add a shared `cycle_<NNN>_candidate_001.abc`: it belongs to the retired
single-agent layout and would blur the Flow/Logic ownership boundary.
