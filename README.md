# Multi-Agent ABC Reproduction

Small-scale reproduction workspace for the paper "Autonomous Evolution of EDA
Tools: Multi-Agent Self-Evolved ABC".

The project reproduces the paper's closed-loop coding-agent evolution. The
current phase implements the Logic Minimization Agent alongside the existing
Flow Agent: an LLM proposes a scoped source diff, the candidate binary is built
in an isolated workspace, CEC-first comparison validates correctness, and
structured feedback drives the next iteration.

## Current Status

- `cycle_000` is the parsed baseline cycle (10 EPFL designs, 9 complete).
- **Benchmark scope expanded** to `large_70`: all 70 sampled designs across
  EPFL, ISCAS, ITC/VTR, and arithmetic families now enter CEC-backed testing.
  BLIF/AIG/bench inputs go directly to ABC; the other 40 Verilog inputs are
  normalized once through Yosys-to-BLIF and reused by baseline, candidate, and
  every frozen evaluation flow.
- **Multi-flow comparison is active** — each design runs the candidate recipe,
  a rewrite/refactor view, and a resub/dc2 view. Per-flow CEC/QoR evidence is
  retained, then median metrics, strict-majority votes, and a no-regression
  guard form the one-row-per-design promotion vector.
- **Planning Agent implemented as the round coordinator** — one model or
  deterministic Planning call freezes exactly two assignments on one baseline:
  `flow_candidate_001` and `logic_candidate_001`. Planner advice is persisted
  and hash-bound to both assignments.
- **Flow and Logic run concurrently in isolated candidate lanes** — Flow owns
  `third_party/FlowTune/src/src/opt`; Logic owns
  `third_party/FlowTune/src/src/base/abci`. Neither lane can write the other's
  source or silently merge patches.
- **All-settled fan-in and strict quorum are implemented** — one branch failure
  does not cancel its sibling. Both branches must settle with lineage-valid
  reviews before portfolio fan-in; promotion additionally requires a real
  candidate build, exact evaluation coverage, and full CEC. Settled negative
  experiment reviews may drive the next Planning round, while coding
  infrastructure failures stop it.
- **No-winner recovery is paper-aligned** — the campaign moves from
  conservative to diverse and then structural exploration, runs a bounded,
  rotating cross-family batch after four consecutive CEC-backed QoR misses, and gives
  Flow and Logic orthogonal hypotheses instead of repeating one inactive
  constant edit.
- **Promotion uses a scalar reward plus a detailed QoR vector** — aggregate AND
  reduction remains supported, while node/depth-product Pareto improvements can
  also promote under bounded regression guardrails. Useful partial trade-offs
  are retained on a non-promoting frontier for a separately rebuilt, fully
  revalidated follow-up candidate.
- **Patch and compile failures self-debug inside one candidate** — strict
  `git apply --check` diagnostics or a bounded compiler-log tail is returned to
  the next model attempt before the branch settles as negative evidence.
- `run.sh` is the one-command Linux entry point. It first checks all pinned
  prior-knowledge repositories, then launches the resumable
  `Planning -> (Flow || Logic) -> portfolio review` loop.
- **Logic Minimization Agent implemented** — strict `src/base/abci` role
  boundary, reachable rewrite/refactor/resub/balance/`dc2` source context,
  isolated diff materialization, distinct-binary compile→CEC→QoR contract,
  dynamic cycle dispatch, and reviewer-driven next-cycle rules. Upstream
  `orchestrate` remains profiled as a coordinator-owned Logic planning target;
  because the pinned FlowTune fork does not register it as an ABC command, the
  target lands in the existing ABCI orchestration wrapper rather than invoking
  a nonexistent command.
- **Paper-style repository profiling expanded to ten pinned repositories** —
  Berkeley ABC, FlowTune, mockturtle, LSOracle, Yosys,
  OpenROAD-flow-scripts, kitty, alice, CUDD, and EQY provide complementary
  source/API, Boolean reasoning, orchestration, metrics, and equivalence priors.
  Planning receives all ten; Logic receives nine external references; Flow
  receives six. Dirty, incomplete, or wrong-revision checkouts cannot inject
  code, and bootstrap verification requires all ten trees.
- Mapper Agent remains a placeholder for a later phase.
- Diagnostic script (`scripts/diagnose_cycles.py`) collects per-cycle
  evidence (review, CEC, QoR deltas) into a JSON bundle for local analysis.

Local macOS development is used for editing, prompt/schema validation, and
Python smoke tests. Full ABC binary execution, candidate compilation, CEC, and
QoR comparison are expected to run after rsyncing the repo to a Linux/ABC host.

This is a correctness-backed foundation for reproducing the paper. The current
campaign has two of the paper's three coding roles, evaluates the eight frozen
technology-independent ABC recipes (`resyn`, `resyn2`, `resyn2a`, `resyn3`,
`compress`, `compress2`, `resyn2rs`, and `compress2rs`) plus a bounded
`ftune_mab_aig_nodes` scheduler lane. After every per-flow CEC pass it also
maps the resulting AIG with the bundled `ASAP7_7nm_LVT_FF` Liberty, runs the
FlowTune sizing/STA sequence, and records post-sizing area plus STA critical
path delay in `asap7_qor_by_flow.csv`. AIG node/depth remain auxiliary signals,
not the final PPA claim. WNS is emitted only when the frozen `asap7_qor`
contract provides a clock period; without that external constraint the report
is deliberately labelled critical-path delay rather than Table-comparable
worst slack.

## Repository Knowledge Bootstrap

The paper makes repository knowledge a first-class cycle-0 input: it profiles
ABC and related repositories before evolution, supplies the resulting profile
and structured Markdown tutorial to the agents, and reports that 68% of token
use went to ABC profiling plus 11% to external repositories. This project uses
a reproducible bounded version of that process rather than relying on repository
names or live web search.

- Ten repositories are pinned by full commit in
  `configs/agents/context/repositories.json`.
- The added priors cover truth-table/decomposition (`kitty`), command/state
  interfaces (`alice`), BDD/cofactor/resource discipline (`CUDD`), and formal
  proof orchestration (`EQY`) in addition to the original ABC/flow references.
- Role-specific hard budgets are 96,000 characters for Planning, 72,000 for
  Logic, and 60,000 for Flow. Source windows are query-ranked and emitted
  round-robin so one repository cannot consume the whole budget.
- Source text is accepted only from an exact, clean checkout with complete
  focus paths. Missing, dirty, incomplete, or wrong-revision repositories use
  their tracked profile only; strict minimum enforcement stops the model call.
- All manifest, profile, checkout, focus, and scanned-file paths are confined
  to this project. Reference paths never enter candidate write scope.

```bash
# Provision exact commits (network access required only when absent).
python3 -B scripts/bootstrap_agent_context.py

# Read-only reproducibility preflight.
python3 -B scripts/bootstrap_agent_context.py --check
```

See `configs/agents/context/README.md` for the repository matrix, licenses,
budget controls, failure modes, and one-repository check/refresh commands.

## Why No Champion Happens

The paper's system gets dense reward feedback: many benchmark suites, multiple
synthesis flows, compile and CEC before QoR, and auxiliary structural, mapping,
STA, and runtime metrics. This reproduction is intentionally smaller, so one
LLM patch plus one flow recipe can easily produce zero deltas. Promotion has two
explicit channels: a regression-free scalar AND-reduction channel and a guarded
node/depth structural Pareto channel. After three consecutive correctness-backed
QoR misses, the scalar channel also permits a one-row/one-node positive
increment under full build, coverage, and CEC so beneficial changes can
accumulate across generations.

The old `CEC 30/70` diagnostic was a missing-frontend issue, not evidence that
the candidate failed 40 extra equivalence checks. `large_70` now keeps all 70
designs in both `benchmark_scope` and `evaluation_benchmark_scope`; its
`unsupported_benchmark_scope` is empty. For each Verilog source, Yosys runs a
deterministic behavioral lowering and writes a candidate-lane BLIF before ABC
starts. A valid remote run therefore reports `70/70` aggregate CEC coverage and
also writes `frontend_summary.csv`, `cec_by_flow.csv`, `qor_delta_by_flow.csv`,
and `flow_vote_summary.csv` for auditability.

The active lineage reported for this campaign has no centralized winner through
cycles 1–5. Earlier single-lane or differently versioned `ACCEPT` artifacts are
historical evidence only; champion status is authoritative only when the
matching `planning/portfolio_review.json` selects it. The unexecuted cycle-6
dispatch is regenerated under the new campaign policy. The current loop then
executes the requested model-free search, uses a separate `probe_NNN` namespace,
and feeds the measured best result plus a diverse family frontier through a
refreshed paired Planning decision before the pending Coding branches run.

Recent implementation issues also made the signal weaker than necessary:

- `cycle_001` was not planner-seeded, so the Flow Agent received a generic
  task instead of a concrete command/source target.
- Command touchpoints were too coarse, making it easy to patch code that the
  evaluated flow did not reach.
- Prompt source context was static and biased toward a few `fxu`/`csw` files.
- CEC used the candidate binary; it now uses the baseline/champion binary to
  keep the correctness checker independent of candidate edits.
- Legacy source-patch scope allowed framework/prompt edits; source diffs are
  now restricted to ABC/FlowTune source plus active-cycle artifacts.
- The scalar AND lane is regression-free and uses relative, absolute, or
  drought-recovery accumulation thresholds. The structural bootstrap/Pareto
  lane instead uses a positive node/depth-product reward with bounded per-design
  node/depth regression guardrails.
- Coding Agent baseline context now comes from authoritative
  `impl_compare/comparison/qor_delta.csv` artifacts. It sees the incumbent
  per-design AND/depth values, previous applied patch, and review feedback.

## Project Structure

```text
try_repo/
  README.md                   project entry point and quickstart
  run.sh                      one-command autonomous loop launcher
  requirements.txt            Python dependencies
  benchmarks/                 sampled benchmark suites (70 frontend-enabled evaluations)
  configs/                    prompts, rules, checklists, flows, evaluation config
    agents/context/           pinned repo manifest + read-only code profiles
  docs/                       structure notes and local paper copy
  experiments/                per-cycle logs, outputs, results, and agent artifacts
  scripts/                    cycle automation, LLM-agent scaffold, diagnostics
    init_cycle.py             bootstrap a new experiment cycle
    bootstrap_agent_context.py provision/check pinned prompt context repos
    diagnose_cycles.py        collect per-cycle evidence for local analysis
    agents/self_evolved_abc/
      planning/               Planning policy and frozen portfolio contracts
        evidence.py             structured cycle evidence reader
        portfolio.py            paired assignments, advice hashes, lineage
        assignment_factory.py   role-normalized assignment construction
      planning_agent.py       LLM-based planner (renders planner_prompt.md)
      coding_agents/          Flow and Logic Agent implementations
      roles/                  exact role registry and lazy dispatch
      workflow/               concurrent branches, manifests, portfolio review
      logic/                  Logic Agent scope and assignment policy
      flow/                   compile/CEC/QoR/review domain stages
  third_party/                external source trees (FlowTune)
  .env                        ignored local model-provider environment
  .local/                     ignored local scratch/archive/run dumps
```

## Local Development

The remote workflow supports Python 3.8 or newer. `run.sh` uses `python3` by
default; set `PYTHON_BIN=/path/to/python3.8` to select an explicit interpreter.

Use local commands for small checks only:

```bash
PYTHONPYCACHEPREFIX=.local/pycache python3 -m compileall -q \
  scripts/agents/self_evolved_abc scripts/init_cycle.py
```

Planning and fixture smoke checks:

```bash
PYTHONPATH=. python3 -B scripts/test_planning_agent.py

PYTHONPATH=. python3 -B scripts/test_logic_minimization_agent.py

PYTHONPATH=. python3 -B scripts/test_dual_agent_loop.py

PYTHONPATH=. python3 -B scripts/test_coding_agent_retry.py

PYTHONPATH=. python3 -B scripts/test_planning_portfolio_evidence.py

PYTHONPATH=. python3 -B scripts/test_python38_compat.py

PYTHONPATH=. python3 -B scripts/test_verilog_frontend_multi_flow.py

PYTHONPATH=. python3 -B scripts/test_frontend_cli_passthrough.py

PYTHONPATH=. python3 -B scripts/bootstrap_agent_context.py --check

PYTHONPATH=. python3 -B -c "from pathlib import Path; from scripts.agents.self_evolved_abc.cycle_context import CycleContext; from scripts.agents.self_evolved_abc.flow.source_patch_runner import run_validation_fixture_smoke; ctx=CycleContext.from_assignment_file(Path('.').resolve(), Path('experiments/cycle_001/agents/assignments/flow_candidate_001.json')); lines=[]; code=run_validation_fixture_smoke(ctx, lines); print('\n'.join(lines)); raise SystemExit(code)"
```

The checked-in FlowTune binary is a Linux executable. On macOS it may fail with
`exec format error`; that is expected and is not a local test failure.

Initialize a Logic Agent cycle after provisioning its read-only context:

```bash
python3 -B scripts/bootstrap_agent_context.py
python3 -B scripts/init_cycle.py cycle_006 \
  --previous-cycle cycle_005 \
  --agent-name logic_minimization_agent \
  --paper-role "Logic Minimization Agent" \
  --source-patch-mode source_patch_diff
```

See `docs/LOGIC_MINIMIZATION_AGENT.md` for the scope, context matrix, gate
ordering, and Linux execution handoff.

## Remote Quickstart

On the Linux/ABC host after syncing the repository:

```bash
# 1. Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1b. Install or expose Yosys (required for the 40 Verilog inputs)
yosys -V

# 2. Configure model (edit .env with your credentials)
#    EDA_AGENT_MODEL_PROVIDER=deepseek
#    EDA_AGENT_MODEL_BASE_URL=https://api.deepseek.com/v1
#    EDA_AGENT_MODEL_API_KEY=<secret>
#    EDA_AGENT_MODEL_NAME=deepseek-chat
#    EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS=16384  # 32768 is also supported

# 3. Provision and verify the ten pinned cycle-0 knowledge repositories
python3 -B scripts/bootstrap_agent_context.py

# 4. Resume from the first unfinished frontier and stop after cycle 10
bash run.sh

# Optional: change the invocation budget and absolute final cycle.
# Completed lineage-valid history is fast-forwarded without consuming budget.
EDA_AGENT_NEW_CYCLE_BUDGET=4 EDA_AGENT_TARGET_CYCLE=12 bash run.sh
```

`run.sh` wraps `workflow.dual_agent_loop`; branch manifests and content hashes
allow a rerun to reuse only complete, lineage-valid work without overwriting or
trusting stale reviews. `--new-cycle-budget` counts only unfinished evaluation
cycles advanced by the current invocation. After the last paid evaluation, its
review is still consumed into one frozen next-cycle Planning dispatch; that
prepared dispatch runs on the next invocation. The absolute
`--target-cycle` is different: after that cycle's fan-in review the campaign
stops without creating an unused next dispatch. Both defaults are 10, so a
lineage completed through cycle 5 runs cycles 6–10. Legacy `build_status=missing`
reviews and structured provider/model/runtime failures are deliberately
non-resumable, so fixing the environment and rerunning retries only those lanes.
If a retried review changes, an unstarted stale downstream Planning dispatch is
regenerated from the new lineage. A dispatch that already has branch work is
never overwritten; parent-lineage drift is reported for explicit recovery.

After syncing a fresh tree, this quick sanity check should print `70 70 0`:

```bash
python3 - <<'PY'
import json
a = json.load(open("experiments/cycle_001/agents/assignments/flow_candidate_001.json"))
print(len(a["benchmark_scope"]), len(a["evaluation_benchmark_scope"]), len(a["unsupported_benchmark_scope"]))
PY
```

When a completed cycle produces zero deltas or repeated weak evidence,
`run.sh` honors the planner by running deterministic batch search before the
next Coding call. Early batches are filtered to the planner-selected command;
after four consecutive correctness-backed QoR misses, the structural recovery
phase removes that filter and explores all role-valid command families. The
automatic structural stage evaluates at most 12 probes per cycle and rotates
the complete opt-only space across cycles 6–10. The summary carries the best
candidate plus a diverse top-three family frontier,
so Planning receives measured alternatives rather than another single-family
guess. `rewrite` probes stay inside Flow's `src/opt/rwr` ownership, while
csweep/fx and other variants use their own source touchpoints. In particular,
the wide set now includes opt-only, recipe-reached `resub` window probes and
`dc2` DAR rewrite/refactor-default probes that were previously lost when ABCI
wrapper variants were filtered by Flow ownership. When at least one probe
passes every eligibility gate (build, exact-scope CEC, correctness-backed QoR,
and an eligible review decision), its measured summary, winner, and QoR vector
are passed through a refreshed Planning call for both Flow and Logic. If every
reviewed probe fails an eligibility gate, `winner.json`
canonically contains `winner: null`; this is a completed
`no_eligible_probe` result, not a batch crash. The coordinator writes a bounded
`outcome.json` with decision/build counts, CEC status and exit-code histograms,
failed benchmark samples, and review/CEC/log paths. Failed-probe QoR never
enters the winner/frontier or automatic QoR evidence channel. Planning consumes
the negative diagnostics, then the same Flow/Logic round continues without a
baseline update or exact replay. The refresh may change hypotheses and tasks,
but it cannot change their shared baseline or frozen evaluation contract. Only
a missing, incomplete, stale, malformed, or tampered batch—or a failed Planning
refresh—blocks both Coding branches; pending control is resumed on rerun.
If a batch probe passes promotion gates, its exact hash-bound diff is replayed
in the current Flow branch and re-runs build/full CEC/QoR so it participates in
the paired fan-in instead of remaining evidence-only. At the absolute target,
the resulting incumbent is recorded in `planning/final_champion.json`; reaching
the target without any champion returns a nonzero status.
Automatic batch directories are generation-specific:
`<cycle>_planner_flow_wide_<lineage-prefix>`. The lineage binds the parent
portfolio, baseline, evaluation contract, planner advice, selected command,
and the exact source/patch variant space. A naked or copied `winner.json` is
never reusable without its matching manifest, probe assignments, and patch
hashes. The four refreshed Planning artifacts (advice, both assignments, and
plan) are committed through a small roll-forward journal, so interruption
between file replacements is recovered before any branch can resume.

Recommended workflow:

1. Edit and run lightweight Python validation locally.
2. Rsync the repository to the remote Linux/ABC host.
3. Run `bash run.sh` remotely.
4. Rsync `experiments/<cycle>/` artifacts back locally for review and the next
   implementation step.

## Low-API Batch Search

For expensive remote runs, use the deterministic batch search before spending
another model call. It expands one assignment into several source-patch
candidates, evaluates them with the existing S4/S5/review gates, and writes a
compact winner report.

```bash
# Generate several model-free candidate cycles from the current assignment.
python3 -B -m scripts.agents.self_evolved_abc.flow.batch_search \
  --base-assignment experiments/cycle_005/agents/assignments/candidate_001.json \
  --start-cycle probe_020 \
  --batch-id flow_wide_cycle_020 \
  --variant-set flow_wide \
  --target-command resub \
  --benchmark-suite large_70 \
  --force

# Run the generated candidates on the remote Linux/ABC host.
python3 -B -m scripts.agents.self_evolved_abc.flow.batch_search \
  --manifest experiments/batches/flow_wide_cycle_020/manifest.json \
  --run \
  --build-candidate-binary \
  --build-jobs 8 \
  --yosys-bin /opt/yosys/bin/yosys \
  --frontend-timeout-seconds 600

# Rebuild summary.csv and winner.json without rerunning ABC.
python3 -B -m scripts.agents.self_evolved_abc.flow.batch_search \
  --manifest experiments/batches/flow_wide_cycle_020/manifest.json \
  --summarize-only
```

After a targeted or full `flow_wide` batch, retest only the nonzero candidates on the
larger 70-design suite before spending another model call:

```bash
python3 -B -m scripts.agents.self_evolved_abc.flow.batch_search \
  --base-assignment experiments/cycle_005/agents/assignments/candidate_001.json \
  --start-cycle probe_050 \
  --batch-id csweep_retest_cycle_050 \
  --variant-set flow_wide \
  --include-variants csweep_floor_c12_l6,csweep_floor_c12_l8,csweep_floor_c16_l6,csweep_floor_c20_l8,csweep_floor_c20_l10 \
  --benchmark-suite large_70 \
  --force
```

Use `--benchmark-suite standard_30` for the smaller BLIF-only suite, or
`--benchmark-glob` repeatedly for a custom scope. Omit `--target-command` for
the full cross-command batch.

Outputs live in `experiments/batches/<batch-id>/summary.csv`, `winner.json`,
and the derived diagnostic `outcome.json`. A fully reviewed batch may
legitimately have `winner: null`; it remains non-promotable but is consumed as
negative Planning evidence. Automatic batches use
`experiments/probe_NNN/` so normal `cycle_NNN` auto-resume is unaffected; each
probe still uses the standard S4/S5/review artifact layout. Loaded manifests
are fail-closed: their base assignment, complete variant set, source and patch
digests, and winner membership must all match.

## Benchmarks

`benchmarks/` contains 10-design sampled suites for the small reproduction:
`epfl/`, `iscas85/`, `iscas89/`, `iscas99/`, `itc99/`, `vtr/`, `arithmetic/`.
See `benchmarks/SOURCES.md` for source and sampling notes.

Named benchmark suites:

- `epfl_10`: EPFL BLIF only, useful for fast smoke runs.
- `standard_30`: EPFL + ISCAS85 + ISCAS89 BLIF designs.
- `large_70`: all seven 10-design local suites, including Verilog designs.

New cycles can be initialized or regenerated with a named suite:

```bash
python3 -B scripts/init_cycle.py cycle_001 --benchmark-suite large_70 --force

python3 -B -m scripts.agents.self_evolved_abc.flow.next_cycle \
  --assignment experiments/cycle_004/agents/assignments/candidate_001.json \
  --next-cycle cycle_005 \
  --benchmark-suite large_70 \
  --force
```

## Configs

`configs/agents/` is the paper-facing agent configuration layer:

- `prompts/coding_agent_prompt.md`: Flow/Logic Agent prompt with
  paper-aligned instructions, validation schema, mode selection rules.
- `shared/`: programming guidance, rulebase, evaluation contract, feedback
  schema.
- `checklists/`: compile, CEC, and QoR review gates.

`configs/flows/` holds ABC flow recipe files (`.abc` scripts).

## Scripts — Agent Scaffold

```text
scripts/agents/self_evolved_abc/
  planning_agent.py            one paired Planning call
  planning/portfolio.py        frozen dispatch + lineage contracts
  roles/registry.py            exact Flow/Logic role registry
  workflow/dual_agent_loop.py  concurrent multi-cycle coordinator
  workflow/branch_run.py       hash-bound branch resume manifest
  workflow/portfolio_review.py strict quorum + deterministic winner
  cycle_driver.py              internal single-branch entry point
  model_client.py              LLM API boundary (OpenAI-compatible)
  coding_agents/flow_agent.py  Flow Agent with source-file context injection
  coding_agents/logic_minimization_agent.py  strict ABCI Logic Agent
  flow/
    assignment.py            assignment scope normalization + cycle directories
    validation.py              strict JSON schema + scope validation
    materialization.py         artifact writing (.abc / .diff)
    source_patch_runner.py     S4: isolated workspace, git-apply, smoke, make
    implementation_compare.py  S5/F7: CEC-first baseline vs candidate
    review.py                  structured review and promotion gate
    next_cycle.py              legacy focused handoff helper
    iteration_loop.py          thin compatibility wrapper to dual coordinator
    cycle_loop.py              legacy single-role diagnostic driver
    batch_search.py            deterministic low-API source-patch batches
    lineage.py                 champion source/binary path resolution
    promotion.py               shared QoR promotion threshold logic
    contracts.py / paths.py    shared labels, paths, scope constants
  fixtures/                    valid/invalid JSON fixtures for smoke tests
```

`scripts/init_cycle.py` and `scripts/summarize_cycle.py` are cycle bootstrapping
and log-parsing utilities, respectively.

## Experiments

Each paired cycle uses candidate-scoped artifacts:

```text
experiments/cycle_NNN/
  planning/
    portfolio_plan.json        frozen paired dispatch
    planner_advice.json        model/deterministic semantic advice + hash
    portfolio_review.json      sole winner decision
    branch_runs/*.json         resume provenance
  agents/
    assignments/               Flow and Logic assignments
    plans/                     model rationale and entry points
    candidate_changes/         materialization summary + decision
    source_patches/            machine-applicable unified diff
    feedback/                  validation errors + review gate
    rule_updates/              agent-proposed + review rule proposals
  candidates/
    flow_candidate_001/impl_compare/
    logic_candidate_001/impl_compare/
  logs/ outputs/ results/      generated data (gitignored bulk)
```

`cycle_000` is the baseline evidence cycle. A subsequent cycle is generated
only after both branch reviews settle and the centralized portfolio review is
persisted.

`cycle_001` starts in `source_patch_diff` mode with a frozen Flow/Logic pair,
two candidate-scoped implementation roots, and a shared hash-bound
`evaluation_contract`. Each benchmark is judged across the eight frozen static
flows plus an isolated FlowTune MAB scheduler replay;
candidate metrics are median-aggregated only after every flow's CEC result is
available, and a vote cannot hide a per-flow AND regression.
The Flow lane is limited to `third_party/FlowTune/src/src/opt`; the Logic lane
is limited to existing `.c`/`.h` files in
`third_party/FlowTune/src/src/base/abci`. Both use the same frozen AIG recipe
portfolio and CEC-backed QoR review. This reaches the edited rewrite/resub/refactor
command kernels and also exercises the separate `ftune` MAB scheduler with a
bounded AIG-node budget. Each CEC-backed flow output is then passed through the
bundled ASAP7 Liberty mapping/gate-sizing/STA chain; see
`comparison/asap7_qor_by_flow.csv` and `comparison/asap7_qor_summary.json`.
The remaining Table-comparability prerequisite is an explicitly frozen clock
period (for WNS), not an invented default constraint.

## Planning Agent

The Planning Agent normally runs once per new cycle. In `auto`/`model` mode it
uses the LLM to formulate two hypotheses and tasks; deterministic mode supplies
stable fallback advice. If Planning's executable `should_skip_llm` control asks
for a model-free sensitivity batch, Planning is refreshed once with those
measurements before either Coding branch starts. Code, not the model, locks the
candidate identities, source ownership, baseline, benchmark/evaluation
contract, and artifact layout.

### Architecture

```text
previous centralized review + both branch evidence + pinned cycle-0 priors
                               │
                               ▼
                    one Planning Agent call
                               │
                 optional model-free batch +
                 measured-evidence Planning refresh
                               │
                   frozen advice + content hash
                         ┌─────┴─────┐
                         ▼           ▼
                  Flow candidate  Logic candidate
                         └─────┬─────┘
                               ▼
              strict-quorum deterministic portfolio review
                               │
                               ▼
                      next shared winner baseline
```

Planning receives the ten-repository prior-knowledge bundle described above.
The generated advice can change hypotheses and coding tasks only. Any drift in
benchmark scope, evaluation commands, role, candidate ID, or source root is
rejected before a portfolio plan is written.

### Local Validation

```bash
PYTHONPATH=. python3 -B scripts/test_planning_agent.py
```

The focused dual-loop regression additionally checks exact paired dispatch,
true concurrency, sibling completion after failure, strict review quorum,
planner advice binding, deterministic fallback, lineage-safe resume, and
shared-winner continuation.

## Pipeline Stages

```
F0  assignment.py     normalize source_patch_diff scope and active-cycle paths
F1  cycle_driver      model proposes source_patch_diff; each bounded attempt
                      writes a typed status, immutable repair assignment, and
                      per-attempt validation-feedback snapshot with SHA-256;
                      strict patch/compile failures retry in the same candidate
S4d source_patch_runner  apply diff to isolated workspace (git apply --recount)
S4c source_patch_runner  Python smoke gate (py_compile + fixture validation)
S4e source_patch_runner  compile candidate ABC binary in workspace
S5/F7 impl_compare    baseline/champion CEC verification + QoR delta
                      (correctness-backed)
     review.py         classify: REPAIR_VALIDATION | PATCH | SMOKE | COMPILE
                       | REJECT_CEC | REPAIR_QOR | RETAIN_FOR_SYNERGY
                       | ACCEPT_FOR_NEXT_CYCLE
     portfolio_review select at most one lineage-valid winner after both lanes
```

## Review Decisions

| Decision | Meaning |
|----------|---------|
| `CODING_INFRASTRUCTURE_FAILURE` | Provider/model/local runtime failed before an experiment; campaign stops |
| `DEFERRED_BY_AGENT` | Valid evidence-insufficient non-proposal; no patch/build was expected |
| `NEEDS_PLANNER_APPROVAL` | Valid request to change the frozen assignment scope |
| `REPAIR_VALIDATION` | Parsed model JSON still failed local schema/scope checks after repair attempts |
| `REPAIR_PATCH` | Diff context doesn't match real source |
| `REPAIR_SMOKE` | Python smoke gate failed |
| `REPAIR_COMPILE` | C compilation failed |
| `REJECT_CEC` | CEC produced a semantic counterexample / explicit inequivalence |
| `REPAIR_EVALUATION` | CEC/QoR coverage or tooling was inconclusive (timeout, crash, skipped, unparseable, or incomplete metrics) |
| `REPAIR_QOR` | CEC passed but QoR didn't improve |
| `RETAIN_FOR_SYNERGY` | Full CEC passed and the size/depth vector is useful, but this trade-off cannot update the baseline until a fresh combined/follow-up candidate passes every gate |
| `ACCEPT_FOR_NEXT_CYCLE` | Full CEC passed and either the scalar AND lane or guarded structural Pareto lane improved — eligible for centralized selection |

Before build/CEC, `build_status` distinguishes outcomes that previously all
looked like `missing`:

| Build status | Coordinator behavior |
|--------------|----------------------|
| `agent_deferred` | Valid evidence-insufficient result; fan in and let Planning narrow the next task |
| `agent_needs_planner_approval` | Valid scope request; fan in without applying a patch |
| `agent_response_validation_failed` | Local JSON/patch contract remained invalid; fan in exact feedback |
| `agent_patch_apply_check_failed` | Structurally valid diff still did not apply exactly to the frozen baseline after three attempts (two repairs); fan in as `REPAIR_PATCH` |
| `agent_provider_transient_failed` | Retry budget exhausted; stop campaign and retry this lane on resume |
| `agent_provider_permanent_failed` / `agent_provider_configuration_failed` | Stop before another Planning round; fix provider configuration |
| `agent_model_response_failed` | Empty, truncated, invalid, refused, or filtered response; stop with typed attempt evidence |
| `agent_preparation_failed` | Local coding runtime failed; stop and retry after repair |

For `REPAIR_SMOKE`, inspect
`experiments/<cycle>/candidates/<candidate>/impl_compare/candidate_modified/build.log`
first. This is
the Python/fixture smoke gate before ABC CEC/QoR starts; it is usually a harness,
validator, fixture, or assignment-scope issue rather than evidence that a new
ABC source patch is needed.

## Model Client Configuration

Model settings live in `.env` (gitignored). Load with `set -a; source .env; set +a`.

```bash
EDA_AGENT_MODEL_PROVIDER=deepseek
EDA_AGENT_MODEL_BASE_URL=https://api.deepseek.com/v1
EDA_AGENT_MODEL_API_KEY=<secret>
EDA_AGENT_MODEL_NAME=deepseek-chat
EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS=16384    # raise to 32768+ if JSON/diffs truncate
```

The Python client and `run.sh` default to 16384 output tokens. Any explicit
`.env` value is preserved because provider limits differ.

In `json_object` mode the authoritative agent JSON Schema is appended to the
system message. Empty/invalid/truncated responses and transient provider errors
enter the same bounded three-attempt loop as local validation feedback. Auth,
model, request-policy, and configuration errors fail fast. Each attempt is
recorded under
`experiments/<cycle>/agents/attempts/<candidate>/`; the frozen Planning
assignment is never modified during Coding retries. Local response-validation
failures store `attempt_XX.feedback.md`; the terminal exact issue section is
also embedded in the hash-bound branch review and carried through a dedicated,
role-tagged Planning/Coding prompt channel. `json_schema` strict mode fails
locally with an actionable configuration error when an agent schema is not
strict-compatible, instead of relying on a provider-side 400 response.

For `source_patch_diff`, every structurally valid response is also checked with
strict `git apply` in a disposable copy of the exact frozen baseline before any
build. A failed target is added only to the next attempt assignment's key source
context, so the model can regenerate exact hunks without mutating the frozen
Planning assignment. Three failed checks produce `REPAIR_PATCH`; fuzzy patching
and whitespace-relaxed application are not accepted.

Larger output budgets help when the model response is cut off, malformed, or
missing part of a unified diff. They do not usually fix repeated `REPAIR_QOR`
once candidates already compile and pass CEC; at that point use diagnostics and
batch search to widen the source-patch search space.

For offline tests: `EDA_AGENT_MODEL_PROVIDER=fixture`.

## Local-Only Data

`.env` for secrets, `.local/` for machine-specific scratch files. Both ignored.
`third_party/FlowTune/` is treated as external source — patches are applied
only inside `impl_compare/candidate_modified/workspace/`.

See `docs/STRUCTURE.md` for a detailed mapping to the paper workflow.
