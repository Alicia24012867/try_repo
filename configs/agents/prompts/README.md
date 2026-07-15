# Prompt Templates

These prompts are operational templates for the paper-style multi-agent ABC
evolution loop described in "Autonomous Evolution of EDA Tools: Multi-Agent
Self-Evolved ABC".

They are intentionally structured around the paper's vocabulary:

- pre-evolution knowledge bootstrapping
- Planning Agent
- Flow Agent
- Logic Minimization Agent
- Mapping Agent
- ABC Programming Guidance
- compile and correctness pre-checks
- CEC and `dsat` formal feedback
- QoR-driven benchmark evaluation
- champion promotion, rollback, and self-evolving rulebase updates

Replace `{{PLACEHOLDER}}` blocks with cycle-specific context before sending a
prompt to an agent. These placeholders are intentional runtime variables, not
unfinished configuration.

## Templates

- `planner_prompt.md`: decides the next evolution step and assigns scoped work.
- `coding_agent_prompt.md`: guides one coding agent through profiling,
  hypothesis testing, patching, and validation.
- `repair_prompt.md`: focuses only on fixing validation, patch-apply, compile,
  smoke, CEC, runtime, or regression failures from a candidate.
- `review_prompt.md`: evaluates whether a candidate becomes the champion, gets
  repaired, or is rolled back.

## Recommended Use Order

1. Fill `planner_prompt.md` with the current champion, both prior branch
   results, rulebase, pinned prior knowledge, and cycle budget.
2. Validate the planner JSON against the locked envelope and freeze exactly
   two assignments under `experiments/{{CYCLE_ID}}/agents/assignments/`: Flow
   first and Logic second.
3. Render `coding_agent_prompt.md` independently for both isolated roles.
4. Run validation, patch application, compile, smoke, full-scope CEC, QoR, and
   branch review in each lane; one failure does not cancel the sibling.
5. Require both reviews and write one centralized portfolio review.
6. Store model-derived artifacts under `experiments/{{CYCLE_ID}}/agents/` and
   candidate evaluation data under `experiments/{{CYCLE_ID}}/candidates/`.

## Output Protocol

The executable scaffold expects model responses as JSON objects. Markdown
reports are generated after schema validation. Prompt templates therefore
describe both the reasoning requirements and the exact JSON keys expected from
the model.

For the current scaffold:

- Planning Agent JSON is consumed by `planning_agent.py`.
- Coding Agent JSON is consumed by `coding_agents/base_coding_agent.py`.
- Flow and Logic source-patch JSON is validated and materialized through the
  shared strict `flow/validation.py` and `flow/materialization.py` path.
- Repair and review JSON are reserved for the next harness step.
- Any non-JSON prose should be treated as a model-format error.

## Design Principles

- Prompts must preserve the paper's gate order: compile, smoke, CEC or `dsat`,
  QoR/runtime evaluation, review, then optional champion promotion.
- Prompts should ask for one attributable hypothesis per candidate so feedback
  can be mapped back to FlowTune, AIG optimization, or mapping.
- Prompts should expose auxiliary metrics, not only a scalar reward, because the
  paper's loop uses structural and mapped QoR feedback to guide later cycles.
- Rulebase updates are proposals until a review artifact cites evidence.
- First-cycle prompts should favor conservative FlowTune `source_patch_diff`
  candidates that can be applied in an isolated workspace, built remotely, and
  evaluated with CEC-backed QoR.
- Prompt rendering should summarize logs and CSVs; benchmark sources and
  generated outputs should not be copied wholesale into a model call.

## Current Source-Patch Bundle

The active Flow Agent reproduction path is source-code feedback iteration:

- Planner output contains two `source_patch_diff` dispatches with disjoint
  roots: Flow gets only `third_party/FlowTune/src/src/opt`; Logic gets only
  `third_party/FlowTune/src/src/base/abci`.
- The default evaluation flow includes `fx`, `rewrite`, `resub`, `dc2`,
  `csweep`, and `refactor`; use this as a reachability hint when choosing
  source targets.
- Coding output must use `candidate_kind: "source_patch_diff"` and include a
  repository-relative unified diff under `source_patch.diff`.
- Every diff target must also appear in `files_to_write`.
- Local validation covers JSON/schema checks, scope checks, patch application,
  and lightweight Python smoke checks.
- Full candidate ABC build, CEC, and QoR collection are remote Linux/ABC tasks.
- Review feedback should use the precise codes `REPAIR_VALIDATION`,
  `REPAIR_PATCH`, `REPAIR_SMOKE`, `REPAIR_COMPILE`, `REPAIR_EVALUATION`,
  `REJECT_CEC`, `REPAIR_QOR`, or `ACCEPT_FOR_NEXT_CYCLE`.

## Minimal Context Bundle

Each prompt works best when the following artifacts are available:

- current source snapshot or git commit
- current rulebase
- allowed subsystem paths
- compile command and log
- CEC command and log
- benchmark list
- QoR summary table
- runtime budget
- previous accepted and rejected candidate summaries
- pinned related-repository profiles and query-relevant source excerpts

The related-repository layer is configured by
`configs/agents/context/repositories.json` and provisioned with
`scripts/bootstrap_agent_context.py`. It injects commit-labelled, bounded,
read-only context. Missing checkouts fall back to checked-in profiles for
diagnostics, but Planning, Flow, and Logic assignments all enforce their full
role-routed exact-revision/profile count before any model call.

The role budgets are 96,000 characters for Planning, 72,000 for Logic, and
60,000 for Flow, normally with two or three ranked files per trusted
repository; assignments may bound the layer between 2,000 and 160,000
characters and one to ten files. Snippets are emitted round-robin across
repositories and carry explicit truncation markers. Prompt authors must not
silently replace a missing checkout with unpinned web text or copy entire
repositories into one model call.

## First-Cycle Prompt Bundle

For `cycle_001`, provide these evidence files to the Flow Agent:

- `experiments/cycle_000/results/summary.csv`
- `experiments/cycle_000/results/skipped.csv`
- `experiments/cycle_000/results/run_notes.md`
- selected scripts under `experiments/cycle_000/outputs/`
