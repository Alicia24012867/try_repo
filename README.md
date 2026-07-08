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
- The Flow Agent source-patch feedback loop is wired and locally smoke-tested:
  - Model proposes `source_patch_diff` targeting real FlowTune C source.
  - Patch is applied in an isolated workspace, binary built, CEC run.
  - Review decision is generated, and evidence feeds into the next cycle.
  - Multi-cycle loop (`cycle_loop.py`) auto-resumes from the last completed
    cycle — no manual trigger needed.
- Assignment scope is normalized through `flow/assignment.py`, so
  `source_patch_diff` cycles consistently allow `third_party/FlowTune/src/src/opt`
  and active-cycle artifact directories.
- `run.sh` is the one-command entry point: `bash run.sh` on a Linux/ABC host
  starts the full autonomous loop.
- Validation failures are retried with diagnostic feedback, and review
  decisions distinguish *why* a build failed (validation, patch, smoke,
  compile) rather than collapsing everything into `REPAIR_BUILD`.
- Logic Minimization Agent and Mapper Agent are placeholders for later phases.

Local macOS development is used for editing, prompt/schema validation, and
Python smoke tests. Full ABC binary execution, candidate compilation, CEC, and
QoR comparison are expected to run after rsyncing the repo to a Linux/ABC host.

## Project Structure

```text
try_repo/
  README.md                   project entry point and quickstart
  run.sh                      one-command autonomous loop launcher
  requirements.txt            Python dependencies
  benchmarks/                 sampled benchmark suites
  configs/                    prompts, rules, checklists, flows, evaluation config
  docs/                       structure notes and local paper copy
  experiments/                per-cycle logs, outputs, results, and agent artifacts
  scripts/                    cycle automation and LLM-agent scaffold
  third_party/                external source trees (FlowTune)
  .env                        ignored local model-provider environment
  .local/                     ignored local scratch/archive/run dumps
```

## Local Development

Use local commands for small checks only:

```bash
python3 -B -m py_compile \
  scripts/init_cycle.py \
  scripts/agents/self_evolved_abc/flow/assignment.py \
  scripts/agents/self_evolved_abc/flow/validation.py \
  scripts/agents/self_evolved_abc/flow/source_patch_runner.py \
  scripts/agents/self_evolved_abc/coding_agents/flow_agent.py
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

Recommended workflow:

1. Edit and run lightweight Python validation locally.
2. Rsync the repository to the remote Linux/ABC host.
3. Run `bash run.sh` remotely.
4. Rsync `experiments/<cycle>/` artifacts back locally for review and the next
   implementation step.

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
    review.py                  structured review (5 decision types)
    next_cycle.py              evidence-chain handoff to next cycle
    iteration_loop.py          one-cycle pipeline orchestrator
    cycle_loop.py              multi-cycle autonomous driver (--auto-resume)
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

`cycle_001` starts in `source_patch_diff` mode. Its assignment targets
`third_party/FlowTune/src/src/opt` and uses a small EPFL benchmark scope
(`epfl_adder`, `epfl_bar`, `epfl_sqrt`) for the first source-level feedback
loop.

## Pipeline Stages

```
F0  assignment.py     normalize source_patch_diff scope and active-cycle paths
F1  cycle_driver      model proposes source_patch_diff (with retry on failure)
S4d source_patch_runner  apply diff to isolated workspace (git apply --recount)
S4c source_patch_runner  Python smoke gate (py_compile + fixture validation)
S4e source_patch_runner  compile candidate ABC binary in workspace
S5/F7 impl_compare    CEC verification + QoR delta (correctness-backed)
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
