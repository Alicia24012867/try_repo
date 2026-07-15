# Repository Structure

This layout mirrors the organization described in the paper at a small
reproduction scale. The paper does not prescribe an exact public directory tree,
so this repository maps its main concepts into local experiment folders.

## Paper Concept Mapping

- `third_party/FlowTune/`: upstream ABC/FlowTune codebase used as the baseline
  integration substrate.
- `benchmarks/`: benchmark suites used by the evaluation loop.
- `configs/agents/`: paper-facing prompts, contracts, checklists, and rules for
  the agent roles.
- `configs/flows/`: ABC command recipes for baseline, FlowTune, CEC, and later
  mapping or STA flows.
- `configs/evaluation/`: metric definitions, run settings, and aggregation
  conventions.
- `experiments/cycle_000/`: initial non-evolved baseline cycle, corresponding
  to the paper's cycle 0 / pre-evolution evaluation stage.
- `scripts/`: local automation for initializing cycles, summarizing results, and
  running the paper-style LLM agent scaffold.
- `docs/`: project documentation and the local paper reference.
- `.local/`: ignored local-only scratch, archives, binaries, run dumps, and
  pinned reference-repository checkouts used by prompt indexing.

The normal development pattern is local editing and lightweight Python checks,
followed by rsync to a Linux/ABC host for candidate ABC build, CEC, and QoR
evaluation. Generated remote artifacts are synced back under `experiments/`.

## Benchmark Suites

The paper evaluates ISCAS, ITC, EPFL, VTR, and arithmetic blocks. This small
reproduction keeps a 10-design sample for each local suite:

- `benchmarks/epfl/`
- `benchmarks/iscas85/`
- `benchmarks/iscas89/`
- `benchmarks/iscas99/`
- `benchmarks/itc99/`
- `benchmarks/vtr/`
- `benchmarks/arithmetic/`

See `benchmarks/SOURCES.md` for the exact source and sampling notes.

## Agent Scaffold

The prompt/config side describes the paper roles and validation contracts:

- `configs/agents/planner/`: planner role and iteration template.
- `configs/agents/coding/`: FlowTune, logic minimization, and mapping role
  contracts.
- `configs/agents/context/`: reproducible repository manifest and compact
  profiles for the read-only pre-evolution knowledge layer.
- `configs/agents/shared/`: programming guidance, rulebase, evaluation
  contract, and feedback schema.
- `configs/agents/prompts/`: prompt templates.
- `configs/agents/checklists/`: compile, CEC, and QoR gates.

The executable scaffold follows the paper's agent naming:

- `scripts/agents/self_evolved_abc/planning_agent.py`: Planning Agent.
- `scripts/agents/self_evolved_abc/coding_agents/flow_agent.py`: Flow Agent.
- `scripts/agents/self_evolved_abc/coding_agents/logic_minimization_agent.py`:
  Logic Minimization Agent.
- `scripts/agents/self_evolved_abc/coding_agents/mapper_agent.py`: future
  Mapper placeholder; it is not present in the current coding-role registry.
- `scripts/agents/self_evolved_abc/shared/rulebase.py`: Self-Evolved Rulebase
  scaffold.
- `scripts/agents/self_evolved_abc/model_client.py`: LLM API boundary, with
  provider integration intentionally isolated from agent role logic.
- `scripts/agents/self_evolved_abc/cycle_driver.py`: internal single-branch
  execution entry point used by the dual coordinator and diagnostics.
- `scripts/agents/self_evolved_abc/flow/`: Flow Agent loop implementation:
  assignment normalization, validation, materialization, isolated source-patch
  application, candidate workspace ABC build, CEC-first implementation
  comparison, review feedback, and next-cycle handoff.
  `planner_batch.py` executes planner-requested sensitivity probes and writes
  the winner evidence back into the pending assignment, while `batch_search.py`
  owns deterministic source-patch variant generation and evaluation.
  Current implementation comparison uses the assignment's
  `evaluation_benchmark_scope` for CEC-backed promotion and keeps unsupported
  frontend samples visible in `unsupported_benchmark_scope`.
- `scripts/agents/self_evolved_abc/logic/`: Logic Agent role boundary,
  assignment normalization, evaluation recipe, and repository-context policy.
- `scripts/agents/self_evolved_abc/repository_context.py`: bounded profiling
  and query-relevant code excerpt builder for pinned reference repositories.
- `scripts/bootstrap_agent_context.py`: exact-revision context provision/check.

## Repository Knowledge Context

```text
configs/agents/context/
  README.md                    budget, trust, and failure contract
  repositories.json           ten exact commits plus role/focus metadata
  profiles/
    berkeley-abc.md
    flowtune.md
    mockturtle.md
    lsoracle.md
    yosys.md
    openroad-flow-scripts.md
    kitty.md
    alice.md
    cudd.md
    eqy.md
.local/context_repos/          ignored sparse/full read-only checkouts
third_party/FlowTune/          verified local build source
```

The manifest bootstrap minimum is ten. Planning receives all ten repositories
under a 96,000-character hard budget; Logic routes nine external references at
72,000 characters; Flow routes six at 60,000. Source snippets are query-ranked
and emitted round-robin. Coding prompts keep FlowTune in the local source index
so its older APIs remain authoritative.

`repository_context.py` resolves the manifest, profiles, checkout roots, focus
paths, and scanned source files underneath the project root. Only exact, clean,
complete checkouts are scanned. Missing, wrong-revision, incomplete, or dirty
trees produce an explicit profile-only fallback; an assignment that enables
minimum enforcement rejects the prompt before a model call. Reference checkout
paths are never candidate write paths.

Per-cycle agent artifacts live under `experiments/<cycle>/agents/`.

## Automation Scripts

- `scripts/init_cycle.py`: creates a new `experiments/cycle_NNN/` skeleton and
  optional assignment JSON. It dispatches to Flow or Logic role-specific scope
  normalization.
- `scripts/summarize_cycle.py`: parses existing ABC/FlowTune logs into
  `summary.csv`, `skipped.csv`, and `run_notes.md`.

## Documentation

- `README.md`: short project entry point.
- `docs/STRUCTURE.md`: detailed repository structure and paper mapping.
- `docs/paper/2604.15082v1.pdf`: local copy of the reference paper.
- `docs/LOGIC_MINIMIZATION_AGENT.md`: implemented Logic Agent design,
  knowledge context, safety boundaries, and runbook.

## Local-Only Area

`.local/` is ignored by git. Use it for machine-specific state and clutter that
should not shape the reproducible project layout:

- archived accidental copies of the repository
- downloaded temporary source trees
- generated experiment logs and outputs moved out of the tracked skeleton
- local binaries, symlinks, and scratch files

## Experiment Cycles

Each experiment cycle keeps generated artifacts together:

- `logs/`: raw ABC, FlowTune, compile, and CEC logs.
- `outputs/`: generated AIG/BLIF/netlist files.
- `results/`: parsed CSV/JSON summaries and final tables.
- `impl_compare/`: baseline/candidate implementation manifests, isolated
  candidate workspace and binary, CEC logs, QoR delta tables, review decision,
  and comparison summary for source-evolution cycles.

For the first small reproduction, `experiments/cycle_000/` is the parsed
baseline and `experiments/cycle_001/` is the first LLM-agent source-patch cycle
skeleton. The `cycle_001` assignment is in `source_patch_diff` mode and targets
`third_party/FlowTune/src/src/opt` plus `third_party/FlowTune/src/src/base/abci`
while keeping generated artifacts inside the active cycle directory. The default
evaluation flow includes `fx`, `rewrite`, `resub`, `dc2`, `csweep`, and
`refactor` to improve the chance that source patches are exercised.
