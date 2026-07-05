# Flow Agent Plan -- candidate_001

## Rationale

Use a conservative flow distilled from stable cycle_000 FlowTune evidence on EPFL designs. The candidate keeps benchmark IO outside the flow and only schedules existing ABC optimization commands.

## Source Design

cycle_000 epfl_adder and epfl_bar FlowTune scripts

## Entry Points

- configs/flows/cycle_001_candidate_001.abc
- experiments/cycle_001/agents/candidate_changes/candidate_001.md

## Invariants

- Use only existing ABC commands
- Do not read or write benchmark artifacts inside the flow
- Treat QoR as provisional until CEC is wired

## Risk Hotspots

- depth may regress on epfl_sqrt-like designs
- no independent CEC gate is available yet
- FlowTune evidence comes from a small EPFL subset
