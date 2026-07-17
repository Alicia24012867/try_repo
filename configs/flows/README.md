# Generated Candidate Recipes

The paired workflow materializes one deterministic recipe per candidate at
runtime:

- `cycle_<NNN>_flow_candidate_001.abc`
- `cycle_<NNN>_logic_candidate_001.abc`

The recipe named by an assignment's `evaluation_flow_commands` is the
`candidate_recipe` member of the three-flow evaluation portfolio. The other two
recipes are frozen in the assignment as command lists (`rewrite_refactor` and
`resub_dc2`) and are rendered directly by `implementation_compare.py`.

Do not add a shared `cycle_<NNN>_candidate_001.abc`: it belongs to the retired
single-agent layout and would blur the Flow/Logic ownership boundary.
