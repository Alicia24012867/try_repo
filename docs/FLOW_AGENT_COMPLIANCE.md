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

- Champion lineage is centralized in `flow/lineage.py`. Prompt source and
  isolated patch workspaces now use `baseline_ref.source_root` as the same
  authoritative snapshot; missing snapshots and alias drift fail closed rather
  than silently falling back to live vanilla source.
- A structurally valid source diff is strictly apply-checked against a
  disposable frozen-baseline workspace before build. Context mismatch enters
  the same bounded Coding repair loop, whose retry-only assignment promotes the
  failed target into key source context. S4 uses the same strict `git apply`
  flags; fuzzy patch fallback and whitespace relaxation are disabled.
- Promotion thresholds are centralized in `flow/promotion.py`, so review,
  prompts, and initial/next assignments agree on what counts as a champion.
- Promotion has two explicit channels: a regression-free scalar net-AND lane
  with relative/absolute thresholds, and a positive node/depth-product Pareto
  lane with bounded per-design regression guardrails. After three consecutive
  full-CEC QoR misses, the scalar lane also permits a one-row/one-node positive
  increment so correctness-backed gains can accumulate.
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
- `large_70` is fully evaluated: 70 designs remain in both
  `benchmark_scope` and `evaluation_benchmark_scope`. ABC-native inputs are
  read directly; 40 Verilog inputs are normalized once by Yosys to BLIF inside
  the isolated candidate lane. `unsupported_benchmark_scope` is empty unless a
  future unsupported extension is explicitly added.
- S5/F7 now executes eight frozen ABC flow views per benchmark (`resyn`,
  `resyn2`, `resyn2a`, `resyn3`, `compress`, `compress2`, `resyn2rs`, and
  `compress2rs`). It retains detailed
  per-flow CEC/QoR rows and derives the promotion vector using median metrics,
  strict-majority votes, and an all-flow non-regression guard.
- Bootstrap and later replacement candidates use the same two reward channels;
  every accepted candidate must still pass the real build, full frozen-scope
  CEC, complete QoR-row, and channel-specific guardrails.
- Coding Agent QoR context now reads the authoritative S5
  `impl_compare/comparison/qor_delta.csv`, not the legacy flow-only summary
  path, and includes the incumbent vector plus the previous applied patch.
- Evaluation-backed lessons are carried as bounded `evolved_rules` in the next
  assignment and rendered with the static rulebase, so rule updates affect
  later coding behavior instead of remaining inert Markdown artifacts.
- Planner `should_skip_llm` is executable control state. `run.sh` launches a
  model-free `flow_wide` batch in `probe_NNN`: early phases filter by planner
  command, while four consecutive correctness-backed QoR misses trigger an
  at-most-12-probe rotating cross-family structural stage. Cycles 6–10 cover
  the complete opt-only space without making one cycle run all probes. It filters shadowed csweep-default
  variants, uses source-owned reached probes such as `src/opt/rwr` for rewrite,
  and integrates the winner/sensitivity evidence.
  Planning is then refreshed for both branches with the shared baseline and
  evaluation contract unchanged; Coding does not start if either step fails.
  A probe that already passes promotion gates is not left as advisory evidence:
  its SHA-256-bound diff is replayed unchanged in the Flow candidate lane and
  repeats build, full CEC, and QoR before paired fan-in.

## Local Compliance Pass: Planning Agent Integration

- `cycle_001` is planner-seeded with target command `csweep`, target source
  directory `third_party/FlowTune/src/src/opt/csw`, 70-design adaptive
  thresholds, and `_planning_meta` for cross-cycle history.
- The first no-evidence cycle remains executable by the LLM. Batch-search skip
  recommendations are reserved for evidence-backed zero-delta or repeated weak
  signal cycles.
- Flow Agent source context now follows the planner target and extracts nearby
  source windows around command functions, reducing behavior-neutral edits from
  missing context.
- Review still refuses weak follow-up improvements unless they meet the
  configured correctness-backed promotion thresholds after a champion exists.

## Local Compliance Pass: `large_70` Verilog Frontend

- The former `30/70` report was a direct-ABC frontend limitation, not a failed
  candidate equivalence check.
- S5/F7 prepares every `.v` input with a deterministic Yosys behavioral-lower
  to BLIF before any ABC command runs. The one generated BLIF is shared by all
  baseline/candidate and multi-flow comparisons for that source.
- Promotion thresholds are computed from the full 70-design evaluated scope;
  frontend, detailed CEC, vote, and aggregate artifacts make every failure
  attributable to a source, frontend, or flow.

## Historical Three-Design Diagnosis: No Champion After Cycle 004

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
  LLM cycle, then feed its canonical result back into Planning. A reviewed batch
  with no probe passing every eligibility gate (build, exact-scope CEC,
  correctness-backed QoR, and an eligible review decision) records `winner:
  null` plus a bounded `outcome.json`; this is negative diagnostic evidence,
  never QoR winner evidence, and it must not stop the subsequent paired Coding
  round.

## Historical Three-Design Diagnosis: `flow_wide_cycle_020`

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

## Current 30-Design No-Winner Recovery

- Do not infer a champion from older or differently versioned cycle artifacts.
  The active reported lineage completed cycles 1–5 with two settled reviews per
  cycle and no centralized winner; its unexecuted cycle-6 dispatch is stale
  under the new campaign policy and is regenerated before execution.
- Zero-delta edits to large capacity/fanout/window constants are treated as
  reachability evidence. After four consecutive full-CEC QoR misses, Planning
  bans another capacity-only edit and runs a bounded rotating `flow_wide` stage
  over all distinct command families before either coding branch starts.
- The batch returns both its best measured candidate and a diverse top-three
  family frontier. Flow and Logic then receive orthogonal structural hypotheses
  instead of copying the same target into both lanes.
- `flow_wide` now contains real `src/opt` probes for `resub` window fanout/TFI
  depth and the DAR rewrite/refactor defaults repeatedly reached inside `dc2`.
  Earlier ABCI-wrapper variants were removed by Flow ownership filtering, which
  had made the advertised cross-family search substantially narrower.
- Promotion now follows the paper's scalar-reward-plus-vector model. The AND
  lane retains its regression-free aggregate policy; a separate node/depth
  product Pareto lane allows bounded per-design trade-offs. Full candidate
  build, exact-scope CEC, and complete QoR rows remain hard gates.
- A useful but non-promotable size/depth trade-off is persisted as
  `RETAIN_FOR_SYNERGY`. It never changes the champion baseline; any composition
  must be materialized as a fresh isolated candidate and repeat build, full CEC,
  and QoR.
- Strict diff-context and compile failures now use the remaining bounded model
  attempts inside the same candidate, with strict apply diagnostics or a bounded
  compiler-log tail. The complete compile log remains in the attempt directory. This
  converts previously wasted cycles into coding self-debug attempts without
  weakening validation.
- `run.sh` fast-forwards completed lineage, regenerates an unexecuted plan made
  by an older campaign policy, executes through absolute cycle 10 by default,
  and does not create an unused cycle-11 dispatch.

## Paper-Fidelity Limitation

The current Flow lane is a command-kernel surrogate, not yet the paper's full
Flow subsystem. It edits `third_party/FlowTune/src/src/opt`, while the fork's
MAB scheduler is implemented in `src/base/abc/abcBayestune.cpp` and exposed by
the `ftune` command in `src/base/abci/abc.c`. The frozen evaluation recipes do
not call `ftune`. The current evaluator uses the eight standard ABC AIG recipes
but still reports node/depth proxies rather than the paper's ASAP7 timing/area
flow. A current winner is therefore a valid foundation champion, not a claim
that the final paper tables have been reproduced.

## Remaining Remote Evidence Needed

- Candidate binary build logs from the server.
- CEC summary for every evaluated benchmark.
- Correctness-backed QoR delta table against the declared champion.
- Batch `summary.csv`, `winner.json`, and `outcome.json` when low-API search is
  used. For `no_eligible_probe`, inspect the CEC status/exit-code histogram and
  sampled failure paths in `outcome.json`; do not interpret failed-probe QoR.
