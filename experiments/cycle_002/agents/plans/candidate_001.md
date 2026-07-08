# Flow Agent Plan -- candidate_001

## Rationale

The Cycle 1 flow did not improve AND count despite CEC passing, indicating that the applied passes (rewrite, refactor) may have already exhausted local optimizations under default cut budgets. By increasing the minimum number of cuts kept per node in the cut-sweeping pass (csw), we allow more functional equivalences to be discovered, potentially reducing node count without altering the command interface or risk to correctness.

## Source Design

None specified.

## Entry Points

- third_party/FlowTune/src/src/opt/csw/cswCore.c – Csw_Sweep function

## Invariants

- The cut-sweeping substitution maintains functional equivalence – CEC continues to pass.
- The existing command interface for `csw` is unchanged; only the effective minimum cuts per node is raised when the passed value is below 16.
- The patch does not alter the structure of AIGs or the flow of control beyond this single function.

## Risk Hotspots

- Increased runtime if many nodes require more cuts, but EPFL designs are small.
- Possible depth increase if the replacement cuts favor area over depth, but likely minor.
