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
  than accumulated into the champion lineage. Follow-up promotion uses a
  scalar net-AND reward plus a per-design Pareto safeguard: zero regressions,
  breadth met, and either relative or absolute magnitude met.
- Deterministic batch search is model-free and still passes through S4/S5/review;
  it increases feedback density without weakening correctness gates.
- Duplicate `* 2.*` backup files were removed because they were unreferenced and
  could pollute source search, prompt context, and validation reasoning.
- Flow Agent prompt source context is bounded. The model sees a source index and
  selected key snippets instead of every matching `fxu`/`opt` file, improving
  token efficiency and feedback focus.
- Planning metadata is now injected at all assignment entry points:
  `init_cycle.py`, `cycle_loop --auto-resume`, and `next_cycle.py`.
- Flow command touchpoints are split per reachable command (`fx`, `rewrite`,
  `resub`, `dc2`, `csweep`, `refactor`) instead of one coarse shared mapping.
- `source_patch_diff` assignments no longer allow model-generated changes to
  prompt/evaluation harness paths; framework repairs remain outside the Flow
  Agent source-diff loop.
- CEC is run with the baseline/champion ABC binary so the equivalence checker
  is independent of candidate source edits.
- `large_70` now separates tracked benchmark coverage from current
  ABC-native evaluation coverage: 70 designs remain in `benchmark_scope`, 30
  BLIF designs form `evaluation_benchmark_scope`, and 40 Verilog designs are
  listed in `unsupported_benchmark_scope` until a frontend is connected.
- The first correctness-backed positive candidate with no regressions may
  bootstrap the champion lineage; subsequent accepted candidates must beat the
  recorded champion under the promotion thresholds.
- Coding Agent QoR context now reads the authoritative S5
  `impl_compare/comparison/qor_delta.csv`, not the legacy flow-only summary
  path, and includes the incumbent vector plus the previous applied patch.
- Evaluation-backed lessons are carried as bounded `evolved_rules` in the next
  assignment and rendered with the static rulebase, so rule updates affect
  later coding behavior instead of remaining inert Markdown artifacts.
- Planner `should_skip_llm` is executable control state. `run.sh` launches a
  model-free, planner-command-filtered `flow_wide` batch in `probe_NNN`, filters
  shadowed csweep-default variants, covers reached wrapper parameters for
  rewrite/resub/dc2/refactor, and integrates the winner/sensitivity evidence
  before resuming.

## Local Compliance Pass: Planning Agent Integration

- `cycle_001` is planner-seeded with target command `csweep`, target source
  directory `third_party/FlowTune/src/src/opt/csw`, 30-design adaptive
  thresholds, and `_planning_meta` for cross-cycle history.
- The first no-evidence cycle remains executable by the LLM. Batch-search skip
  recommendations are reserved for evidence-backed zero-delta or repeated weak
  signal cycles.
- Flow Agent source context now follows the planner target and extracts nearby
  source windows around command functions, reducing behavior-neutral edits from
  missing context.
- Review still refuses weak follow-up improvements unless they meet the
  configured correctness-backed promotion thresholds after a champion exists.

## Local Compliance Pass: `large_70` Frontend Split

- The remote `30/70` CEC summaries came from counting Verilog samples that the
  current direct-ABC runner cannot read, not from true candidate equivalence
  failures on those designs.
- S5/F7 now iterates over `evaluation_benchmark_scope`, so unsupported frontend
  rows no longer create false `REJECT_CEC` decisions.
- Promotion thresholds are computed from the evaluated scope. For current
  `large_70` assignments this is 30 designs; the 70-design threshold tier should
  be used only after Verilog conversion/read support is added.

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

## Remote Diagnosis: Corrected 30-Design Run

- `cycle_001` passed CEC `30/30` and bootstrapped the champion with total AND
  delta `-6`, three improved rows, and no regressions.
- `cycle_002` inherited that champion correctly but produced one improvement
  and one regression for net zero, so rollback was correct.
- `cycle_003` through `cycle_005` passed CEC but produced exact zero structural
  deltas. Their edits changed large capacity/fanout/window constants without
  evidence that the limiting conditions were active.
- The assignments already contained `should_skip_llm: true`, but the launcher
  treated it as advisory. Automatic batch execution and feedback integration
  now close that planner-to-executor control gap.
- The former three-way promotion conjunction incorrectly reused the paper's
  long-run percentage result as a per-cycle hard requirement. Promotion now
  follows the paper's scalar-reward-plus-vector model while retaining strict
  no-regression and breadth gates.

## Remaining Remote Evidence Needed

- Candidate binary build logs from the server.
- CEC summary for every evaluated benchmark.
- Correctness-backed QoR delta table against the declared champion.
- Batch `summary.csv` and `winner.json` when low-API search is used.
