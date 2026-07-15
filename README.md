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
- **Benchmark scope expanded** to `large_70`: 70 sampled designs are tracked
  across EPFL, ISCAS, ITC/VTR, and arithmetic families. The current ABC-native
  S5/F7 runner evaluates the 30 BLIF designs for CEC-backed promotion and
  records the 40 Verilog designs as frontend-pending.
- **Planning Agent implemented as the round coordinator** — one model or
  deterministic Planning call freezes exactly two assignments on one baseline:
  `flow_candidate_001` and `logic_candidate_001`. Planner advice is persisted
  and hash-bound to both assignments.
- **Flow and Logic run concurrently in isolated candidate lanes** — Flow owns
  `third_party/FlowTune/src/src/opt`; Logic owns
  `third_party/FlowTune/src/src/base/abci`. Neither lane can write the other's
  source or silently merge patches.
- **All-settled fan-in and strict quorum are implemented** — one branch failure
  does not cancel its sibling, but both candidate reviews must be complete,
  lineage-valid, full-scope CEC-backed results before the centralized portfolio
  review can select a winner or generate the next Planning round.
- `run.sh` is the one-command Linux entry point. It first checks all pinned
  prior-knowledge repositories, then launches the resumable
  `Planning -> (Flow || Logic) -> portfolio review` loop.
- **Logic Minimization Agent implemented** — strict `src/base/abci` role
  boundary, reachable rewrite/refactor/resub/balance/`dc2` source context,
  isolated diff materialization, distinct-binary compile→CEC→QoR contract,
  dynamic cycle dispatch, and reviewer-driven next-cycle rules. Upstream
  `orchestrate` remains profiled but is not auto-targeted because the pinned
  FlowTune fork does not register that command.
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
LLM patch plus one flow recipe can easily produce zero deltas or a one-row
improvement. A candidate that improves only one benchmark by a few AND nodes is
weak evidence for replacing an existing champion, even though the first
correctness-backed positive, no-regression candidate may bootstrap the initial
champion lineage.

The recent `CEC 30/70` diagnostic was a harness-front-end issue, not evidence
that the candidate failed 40 extra equivalence checks. `large_70` includes 40
Verilog files, while the current implementation-comparison script invokes ABC
directly and reliably supports only ABC-native `.blif/.bench/.aig` inputs. The
assignment now keeps:

- `benchmark_scope`: all 70 tracked paper-family samples.
- `evaluation_benchmark_scope`: the 30 ABC-native designs used for current
  CEC-backed promotion.
- `unsupported_benchmark_scope`: the 40 Verilog designs waiting for a
  Verilog/Yosys frontend.

This means a valid remote run should report CEC coverage such as `30/30`, not
`30/70`, until the Verilog frontend is implemented.

The latest corrected remote run produced one valid bootstrap champion:

- `cycle_001`: `ACCEPT_FOR_NEXT_CYCLE`, CEC `30/30`, total AND delta `-6`,
  improved/regressed/unchanged `3/0/27`.
- `cycle_002`: CEC `30/30`, but net delta `0` with `1/1/28`; this is not a safe
  replacement champion.
- `cycle_003` through `cycle_005`: CEC `30/30`, but all 30 rows were unchanged.

The zero-delta patches enlarged `fx` capacity, a rewrite fanout ceiling, and a
resubstitution window without evidence that those limits were active. The
planner requested batch search, but the old loop only printed that request and
continued calling the model. The current loop executes that control decision,
uses a separate `probe_NNN` namespace, and feeds `summary.csv`, `winner.json`,
and the winner QoR vector into the pending cycle.

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
- The first correctness-backed positive, no-regression candidate can bootstrap
  the champion lineage. Later candidates are compared against that champion and
  must be regression-free, meet the benchmark-breadth gate, and meet either the
  configured relative or absolute AND-reduction magnitude gate.
- Coding Agent baseline context now comes from authoritative
  `impl_compare/comparison/qor_delta.csv` artifacts. It sees the incumbent
  per-design AND/depth values, previous applied patch, and review feedback.

## Project Structure

```text
try_repo/
  README.md                   project entry point and quickstart
  run.sh                      one-command autonomous loop launcher
  requirements.txt            Python dependencies
  benchmarks/                 sampled benchmark suites (70 tracked, 30 ABC-native evaluated)
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

PYTHONPATH=. python3 -B scripts/test_python38_compat.py

PYTHONPATH=. python3 -B scripts/bootstrap_agent_context.py --check

PYTHONPATH=. python3 -B -c "from pathlib import Path; from scripts.agents.self_evolved_abc.cycle_context import CycleContext; from scripts.agents.self_evolved_abc.flow.source_patch_runner import run_validation_fixture_smoke; ctx=CycleContext.from_assignment_file(Path('.').resolve(), Path('experiments/cycle_001/agents/assignments/candidate_001.json')); lines=[]; code=run_validation_fixture_smoke(ctx, lines); print('\n'.join(lines)); raise SystemExit(code)"
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

# 2. Configure model (edit .env with your credentials)
#    EDA_AGENT_MODEL_PROVIDER=deepseek
#    EDA_AGENT_MODEL_BASE_URL=https://api.deepseek.com/v1
#    EDA_AGENT_MODEL_API_KEY=<secret>
#    EDA_AGENT_MODEL_NAME=deepseek-chat
#    EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS=16384  # 32768 is also supported

# 3. Provision and verify the ten pinned cycle-0 knowledge repositories
python3 -B scripts/bootstrap_agent_context.py

# 4. Launch the autonomous loop (from cycle_001, max 5 cycles)
bash run.sh
```

`run.sh` wraps `workflow.dual_agent_loop`; branch manifests and content hashes
allow a rerun to reuse only complete, lineage-valid work without overwriting or
trusting stale reviews.

After syncing a fresh tree, this quick sanity check should print `70 30 40`:

```bash
python3 - <<'PY'
import json
a = json.load(open("experiments/cycle_001/agents/assignments/candidate_001.json"))
print(len(a["benchmark_scope"]), len(a["evaluation_benchmark_scope"]), len(a["unsupported_benchmark_scope"]))
PY
```

When a completed cycle produces zero deltas or repeated weak evidence,
`run.sh` honors the planner by running deterministic batch search before the
next LLM call. The automatic batch is filtered to the planner-selected command;
`flow_wide` includes reached wrapper probes for `rewrite`, `resub`, `dc2`, and
`refactor` in addition to csweep/fx. Pass `--honor-planner-skip-llm` without
`--auto-batch-on-planner-skip` when a diagnostic run should stop at that point
instead.

The repeated-decision guard is configurable. By default the loop stops after
three repeated review decisions, which means four same-decision cycles in a
row. Use `--same-decision-repeat-limit 0` for an uninterrupted remote run, or
resume from the printed pending assignment if the guard stops after generating
the next cycle.

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
  --build-jobs 8

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

Outputs live in `experiments/batches/<batch-id>/summary.csv` and
`experiments/batches/<batch-id>/winner.json`. Automatic batches use
`experiments/probe_NNN/` so normal `cycle_NNN` auto-resume is unaffected; each
probe still uses the standard S4/S5/review artifact layout.

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

`cycle_001` starts in `source_patch_diff` mode with a frozen Flow/Logic pair.
The Flow lane is limited to `third_party/FlowTune/src/src/opt`; the Logic lane
is limited to existing `.c`/`.h` files in
`third_party/FlowTune/src/src/base/abci`. Both use the same benchmark and
promotion flow, which reaches FlowTune and rewrite/resub/refactor families
before CEC-backed QoR review.

## Planning Agent

The Planning Agent runs once per new cycle. In `auto`/`model` mode it uses the
LLM to formulate two hypotheses and tasks; deterministic mode supplies stable
fallback advice. Code, not the model, locks the candidate identities, source
ownership, baseline, benchmark/evaluation contract, and artifact layout.

### Architecture

```text
previous centralized review + both branch evidence + pinned cycle-0 priors
                               │
                               ▼
                    one Planning Agent call
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
F1  cycle_driver      model proposes source_patch_diff (with retry on failure)
S4d source_patch_runner  apply diff to isolated workspace (git apply --recount)
S4c source_patch_runner  Python smoke gate (py_compile + fixture validation)
S4e source_patch_runner  compile candidate ABC binary in workspace
S5/F7 impl_compare    baseline/champion CEC verification + QoR delta
                      (correctness-backed)
     review.py         classify: REPAIR_VALIDATION | PATCH | SMOKE | COMPILE
                       | REJECT_CEC | REPAIR_QOR | ACCEPT_FOR_NEXT_CYCLE
     portfolio_review select at most one lineage-valid winner after both lanes
```

## Review Decisions

| Decision | Meaning |
|----------|---------|
| `REPAIR_VALIDATION` | Model JSON failed schema/scope checks |
| `REPAIR_PATCH` | Diff context doesn't match real source |
| `REPAIR_SMOKE` | Python smoke gate failed |
| `REPAIR_COMPILE` | C compilation failed |
| `REJECT_CEC` | CEC equivalence check failed |
| `REPAIR_QOR` | CEC passed but QoR didn't improve |
| `ACCEPT_FOR_NEXT_CYCLE` | CEC passed AND QoR improved — bootstrap or replacement champion |

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
