# Multi-Agent ABC Reproduction

Small-scale reproduction workspace for the paper "Autonomous Evolution of EDA
Tools: Multi-Agent Self-Evolved ABC".

The current objective is to reproduce the paper's closed-loop Flow Agent
self-evolution: an LLM proposes source-level changes to the FlowTune ABC
subsystem, the candidate binary is built in an isolated workspace, CEC-first
implementation comparison validates correctness, and structured feedback drives
the next iteration — all without human intervention.

## Current Status

- `cycle_000` is the parsed baseline cycle (10 EPFL designs, 9 complete).
- **Benchmark scope expanded** from 3 EPFL to 30 designs (EPFL + ISCAS85 + ISCAS89).
- **Planning Agent implemented** — deterministic planning engine selects strategy,
  target command, source directory, and adaptive thresholds for each cycle.
- **Planning Agent wired into execution** — `init_cycle.py`, `cycle_loop
  --auto-resume`, and `next_cycle.py` now seed assignments with deterministic
  planning metadata before the Flow Agent is called.
- The Flow Agent source-patch feedback loop is wired and locally smoke-tested:
  - Model proposes `source_patch_diff` targeting real FlowTune C source.
  - Patch is applied in an isolated workspace, binary built, CEC run.
  - Review decision is generated, and evidence feeds into the next cycle.
  - Planning Engine generates `planner_hypothesis` with adaptive thresholds.
  - Prompt source context follows the planner-selected command and includes
    targeted source excerpts around reachable command functions.
  - Multi-cycle loop (`cycle_loop.py`) auto-resumes from the last completed
    cycle — no manual trigger needed.
- Assignment scope is normalized through `flow/assignment.py`, so
  `source_patch_diff` cycles consistently allow FlowTune `opt/`, the relevant
  `base/abci/` command wrappers, and active-cycle artifact directories while
  blocking model-written changes to prompts or evaluation harness code.
- `run.sh` is the one-command entry point: `bash run.sh` on a Linux/ABC host
  starts the full autonomous loop.
- Validation failures are retried with diagnostic feedback, and review
  decisions distinguish *why* a build failed (validation, patch, smoke,
  compile) rather than collapsing everything into `REPAIR_BUILD`.
- Logic Minimization Agent and Mapper Agent are placeholders for later phases.
- Diagnostic script (`scripts/diagnose_cycles.py`) collects per-cycle
  evidence (review, CEC, QoR deltas) into a JSON bundle for local analysis.

Local macOS development is used for editing, prompt/schema validation, and
Python smoke tests. Full ABC binary execution, candidate compilation, CEC, and
QoR comparison are expected to run after rsyncing the repo to a Linux/ABC host.

## Why No Champion Happens

The paper's system gets dense reward feedback: many benchmark suites, multiple
synthesis flows, compile and CEC before QoR, and auxiliary structural, mapping,
STA, and runtime metrics. This reproduction is intentionally smaller, so one
LLM patch plus one flow recipe can easily produce zero deltas or a one-row
improvement. A candidate that improves only one benchmark by a few AND nodes is
weak evidence and should not become a champion.

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

## Project Structure

```text
try_repo/
  README.md                   project entry point and quickstart
  run.sh                      one-command autonomous loop launcher
  requirements.txt            Python dependencies
  benchmarks/                 sampled benchmark suites (30 .blif designs)
  configs/                    prompts, rules, checklists, flows, evaluation config
  docs/                       structure notes and local paper copy
  experiments/                per-cycle logs, outputs, results, and agent artifacts
  scripts/                    cycle automation, LLM-agent scaffold, diagnostics
    init_cycle.py             bootstrap a new experiment cycle
    diagnose_cycles.py        collect per-cycle evidence for local analysis
    agents/self_evolved_abc/
      planning/               **Planning Agent** (deterministic engine)
        evidence.py             structured cycle evidence reader
        strategy.py             command/source targeting + strategy selection
        thresholds.py           adaptive promotion threshold management
        engine.py               deterministic planning engine
      planning_agent.py       LLM-based planner (renders planner_prompt.md)
      coding_agents/          Flow/Logic/Mapper Agent implementations
      flow/                   pipeline stages (S4/S5/review/next_cycle)
  third_party/                external source trees (FlowTune)
  .env                        ignored local model-provider environment
  .local/                     ignored local scratch/archive/run dumps
```

## Local Development

Use local commands for small checks only:

```bash
python3 -B -m py_compile \
  scripts/agents/self_evolved_abc/cycle_context.py \
  scripts/init_cycle.py \
  scripts/agents/self_evolved_abc/flow/assignment.py \
  scripts/agents/self_evolved_abc/flow/lineage.py \
  scripts/agents/self_evolved_abc/flow/promotion.py \
  scripts/agents/self_evolved_abc/flow/validation.py \
  scripts/agents/self_evolved_abc/flow/source_patch_runner.py \
  scripts/agents/self_evolved_abc/flow/review.py \
  scripts/agents/self_evolved_abc/flow/next_cycle.py \
  scripts/agents/self_evolved_abc/flow/batch_search.py \
  scripts/agents/self_evolved_abc/flow/implementation_compare.py \
  scripts/agents/self_evolved_abc/flow/cycle_loop.py \
  scripts/agents/self_evolved_abc/planning/engine.py \
  scripts/agents/self_evolved_abc/planning/strategy.py \
  scripts/agents/self_evolved_abc/planning_agent.py \
  scripts/agents/self_evolved_abc/coding_agents/flow_agent.py
```

Planning and fixture smoke checks:

```bash
PYTHONPATH=. python3 -B scripts/test_planning_agent.py

PYTHONPATH=. python3 -B -c "from pathlib import Path; from scripts.agents.self_evolved_abc.cycle_context import CycleContext; from scripts.agents.self_evolved_abc.flow.source_patch_runner import run_validation_fixture_smoke; ctx=CycleContext.from_assignment_file(Path('.').resolve(), Path('experiments/cycle_001/agents/assignments/candidate_001.json')); lines=[]; code=run_validation_fixture_smoke(ctx, lines); print('\n'.join(lines)); raise SystemExit(code)"
```

The checked-in FlowTune binary is a Linux executable. On macOS it may fail with
`exec format error`; that is expected and is not a local test failure.

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
#    EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS=16384

# 3. Launch the autonomous loop (from cycle_001, max 5 cycles)
bash run.sh
```

`run.sh` wraps `cycle_loop.py --auto-resume`, so running it again continues
from the last completed cycle without overwriting any data.

When a completed cycle produces zero deltas or repeated weak evidence, the
planner may recommend batch search before another LLM call. The loop prints
that recommendation; pass `--honor-planner-skip-llm` to `cycle_loop.py` if you
want the remote run to stop before spending the next model call.

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
  --start-cycle cycle_020 \
  --batch-id flow_wide_cycle_020 \
  --variant-set flow_wide \
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

After a full `flow_wide` batch, retest only the nonzero candidates on all EPFL
benchmarks before spending another model call:

```bash
python3 -B -m scripts.agents.self_evolved_abc.flow.batch_search \
  --base-assignment experiments/cycle_005/agents/assignments/candidate_001.json \
  --start-cycle cycle_050 \
  --batch-id csweep_retest_cycle_050 \
  --variant-set flow_wide \
  --include-variants csweep_floor_c12_lkeep,csweep_floor_c16_lkeep,csweep_default_c6_l5,csweep_default_c12_l6,csweep_default_c16_l6 \
  --benchmark-glob 'benchmarks/epfl/*.blif' \
  --force
```

If that EPFL retest shows improvements on multiple benchmarks, repeat with an
additional `--benchmark-glob 'benchmarks/iscas85/*.blif'` to check whether the
signal generalizes beyond EPFL.

Outputs live in `experiments/batches/<batch-id>/summary.csv` and
`experiments/batches/<batch-id>/winner.json`. Generated cycles use normal
`experiments/cycle_NNN/` artifacts, so they can be inspected with the same
review and implementation-compare tooling as LLM-generated cycles.

## Benchmarks

`benchmarks/` contains 10-design sampled suites for the small reproduction:
`epfl/`, `iscas85/`, `iscas89/`, `iscas99/`, `itc99/`, `vtr/`, `arithmetic/`.
See `benchmarks/SOURCES.md` for source and sampling notes.

## Configs

`configs/agents/` is the paper-facing agent configuration layer:
- `prompts/coding_agent_prompt.md`: Flow/Logic/Mapper Agent prompt with
  paper-aligned instructions, validation schema, mode selection rules.
- `shared/`: programming guidance, rulebase, evaluation contract, feedback
  schema.
- `checklists/`: compile, CEC, and QoR review gates.

`configs/flows/` holds ABC flow recipe files (`.abc` scripts).

## Scripts — Agent Scaffold

```text
scripts/agents/self_evolved_abc/
  cycle_driver.py              single-cycle agent entry point
  model_client.py              LLM API boundary (OpenAI-compatible)
  coding_agents/flow_agent.py  Flow Agent with source-file context injection
  flow/
    assignment.py            assignment scope normalization + cycle directories
    validation.py              strict JSON schema + scope validation
    materialization.py         artifact writing (.abc / .diff)
    source_patch_runner.py     S4: isolated workspace, git-apply, smoke, make
    implementation_compare.py  S5/F7: CEC-first baseline vs candidate
    review.py                  structured review and promotion gate
    next_cycle.py              evidence-chain handoff to next cycle
    iteration_loop.py          one-cycle pipeline orchestrator
    cycle_loop.py              multi-cycle autonomous driver (--auto-resume)
    batch_search.py            deterministic low-API source-patch batches
    lineage.py                 champion source/binary path resolution
    promotion.py               shared QoR promotion threshold logic
    contracts.py / paths.py    shared labels, paths, scope constants
  fixtures/                    valid/invalid JSON fixtures for smoke tests
```

`scripts/init_cycle.py` and `scripts/summarize_cycle.py` are cycle bootstrapping
and log-parsing utilities, respectively.

## Experiments

Each cycle keeps the same structure:

```text
experiments/cycle_NNN/
  agents/
    assignments/               planner input for this cycle
    plans/                     model rationale and entry points
    candidate_changes/         materialization summary + decision
    source_patches/            machine-applicable unified diff
    feedback/                  validation errors + review gate
    rule_updates/              agent-proposed + review rule proposals
  impl_compare/
    baseline_unmodified/       S4 manifest + build log
    candidate_modified/        S4 manifest + patch.diff + workspace/
    comparison/                CEC/QoR CSVs, review_decision.json, summary
  logs/ outputs/ results/      generated data (gitignored bulk)
```

`cycle_000` is the baseline evidence cycle. All subsequent cycles are generated
automatically by `next_cycle.py` at the end of each iteration.

`cycle_001` starts in `source_patch_diff` mode. Its assignment is seeded by the
deterministic Planning Engine, targets `csweep` first, and evaluates the 30
design EPFL + ISCAS85 + ISCAS89 scope. Source edits are limited to
`third_party/FlowTune/src/src/opt` plus the relevant
`third_party/FlowTune/src/src/base/abci` command wrappers. The default
evaluation flow includes `fx`, `rewrite`, `resub`, `dc2`, `csweep`, and
`refactor` so source patches have a better chance to be exercised before
CEC-backed QoR review.

## Planning Agent

The Planning Agent drives Flow Agent self-evolution through a deterministic
rule-based engine. It is wired into `init_cycle.py`, `cycle_loop --auto-resume`,
and `next_cycle.py`, so both first-cycle and follow-up assignments carry the
same planner contract.

### Architecture

```
Evidence (review_decision.json, qor_delta.csv, cec_summary.csv)
    │
    ▼
PlanningEngine.plan()
    ├── read_cycle_evidence()     → CycleEvidence
    ├── reconstruct history       → prior commands tried, champion count
    ├── propose_thresholds()      → adaptive (scope-aware, early-cycle lenient)
    ├── select_strategy()         → Strategy (task_type, target_command, …)
    └── _build_hypothesis()       → planner_hypothesis text
    │
    ▼
next_cycle assignment  (planner_hypothesis, thresholds, discouraged_targets, _planning_meta)
```

Auto-resume also backfills this metadata for older checked-in assignments, so
`cycle_001` no longer starts from a generic, unplanned Flow Agent task.

### Strategy Routing

| Evidence | Strategy | Action |
|----------|----------|--------|
| No prior cycles | `optimization` | Default: csweep, execute first planned LLM cycle |
| Build/CEC failure | `repair` | Fix the gate, carry discouraged targets |
| Champion promoted | `optimization` | Exploit same command, vary parameter |
| REPAIR_QOR + zero delta | `optimization` | Switch to untried command, batch first |
| REPAIR_QOR + partial improvement | `optimization` | Amplify same command, may relax thresholds |
| REPAIR_QOR + regressions | `optimization` | Switch command, force batch_search |

### Adaptive Thresholds

Thresholds scale with benchmark scope and tighten as champions accumulate:

| Scope | Cycle | avg≥ | total≥ | improved≥ |
|-------|-------|------|--------|-----------|
| 10 designs | early | 3.0% | 10 | 1 |
| 30 designs | early | 1.8% | 15 | 3 |
| 30 designs | normal | 3.0% | 15 | 3 |
| 30 designs | 3+ champs | 3.6% | 15 | 3 |
| 70 designs | normal | 2.0% | 20 | 5 |

### Local Validation

```bash
PYTHONPATH=. python3 -B scripts/test_planning_agent.py
```

Covers 132 assertions across 9 sections: paper compliance, evidence reading,
strategy routing (all 7 branches), threshold adaptation (all branches),
engine operations, next_cycle integration, LLM planner, and edge cases.

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
     next_cycle.py     generate next-cycle assignment with evidence chain
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
| `ACCEPT_FOR_NEXT_CYCLE` | CEC passed AND QoR improved — champion |

## Model Client Configuration

Model settings live in `.env` (gitignored). Load with `set -a; source .env; set +a`.

```bash
EDA_AGENT_MODEL_PROVIDER=deepseek
EDA_AGENT_MODEL_BASE_URL=https://api.deepseek.com/v1
EDA_AGENT_MODEL_API_KEY=<secret>
EDA_AGENT_MODEL_NAME=deepseek-chat
EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS=16384    # needed for unified diffs
```

For offline tests: `EDA_AGENT_MODEL_PROVIDER=fixture`.

## Local-Only Data

`.env` for secrets, `.local/` for machine-specific scratch files. Both ignored.
`third_party/FlowTune/` is treated as external source — patches are applied
only inside `impl_compare/candidate_modified/workspace/`.

See `docs/STRUCTURE.md` for a detailed mapping to the paper workflow.
