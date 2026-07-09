# Flow Agent Compliance Check

This note records the local theory/compliance pass for the paper reproduction.
It is intentionally lightweight: remote ABC compilation, CEC, and QoR runs stay
on the Linux server.

## Paper-Aligned Requirements

- Preserve ABC as one integrated binary and command interface.
- Keep correctness as a hard gate: compile/smoke first, CEC before QoR.
- Compare candidates against the current champion, not stale vanilla state.
- Retain and accumulate only beneficial correctness-backed changes.
- Feed dense evidence back to the next cycle: build status, CEC rows, QoR
  deltas, touched files, and rule/update rationale.
- Prefer source changes with structural precedent in ABC and reachability from
  the evaluation flow.

## Local Compliance Decisions

- Champion lineage is centralized in `flow/lineage.py`, so source context,
  isolated workspaces, and baseline binary selection use the same assignment
  fields.
- Promotion thresholds are centralized in `flow/promotion.py`, so review,
  prompts, and initial/next assignments agree on what counts as a champion.
- Weak one-row or one-node improvements are treated as repair feedback rather
  than accumulated into the champion lineage.
- Deterministic batch search is model-free and still passes through S4/S5/review;
  it increases feedback density without weakening correctness gates.
- Duplicate `* 2.*` backup files were removed because they were unreferenced and
  could pollute source search, prompt context, and validation reasoning.
- Flow Agent prompt source context is bounded. The model sees a source index and
  selected key snippets instead of every matching `fxu`/`opt` file, improving
  token efficiency and feedback focus.

## Remote Diagnosis: No Champion After Cycle 004

- Build and CEC passed in cycles 001-004, so the blocker is not correctness or
  compilation.
- Cycles 001-003 only improved one benchmark (`epfl_sqrt`) with total AND
  reductions of -3, -1, and -1; `epfl_adder` and `epfl_bar` stayed unchanged.
- Cycle 004 touched `fxuSelect.c` lookahead and produced zero AND/depth delta on
  all three benchmarks, which is a reachability/behavior-neutral signal.
- The run did not execute deterministic batch search (`experiments/batches/`
  was empty), so the system spent model calls on repeated narrow candidates
  rather than using CPU to sweep parameter space.
- Next step should use `batch_search --variant-set flow_wide` before another
  LLM cycle, then feed the batch `summary.csv`/`winner.json` back into planning.

## Remote Diagnosis: `flow_wide_cycle_020`

- The 24-candidate deterministic sweep completed without finding a champion.
- All `fx_*` candidates produced zero AND delta, including command defaults,
  selector mode switches, and lookahead sweeps. Under the current evaluation
  flow and benchmark set, this family is behavior-neutral and should not receive
  more model/API budget until the flow explicitly exercises a different `fx`
  mechanism.
- `csweep` is the only source family with nonzero signal. The best candidates
  reduce total AND count by 3 with no regressions, but still improve only one of
  three benchmark rows, so they correctly fail the champion threshold.
- The result points to benchmark sparsity and low feedback density rather than
  broken correctness plumbing. Retest the top `csweep` candidates on a wider
  benchmark scope before relaxing promotion thresholds or starting another LLM
  source-edit cycle.
- `batch_search` supports `--include-variants` and repeated `--benchmark-glob`
  arguments so this retest can focus on the known nonzero candidates instead of
  rerunning the whole 24-candidate grid.

## Remaining Remote Evidence Needed

- Candidate binary build logs from the server.
- CEC summary for every evaluated benchmark.
- Correctness-backed QoR delta table against the declared champion.
- Batch `summary.csv` and `winner.json` when low-API search is used.
