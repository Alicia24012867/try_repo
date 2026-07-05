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
- `repair_prompt.md`: focuses only on fixing compile, smoke, CEC, runtime, or
  regression failures from a candidate.
- `review_prompt.md`: evaluates whether a candidate becomes the champion, gets
  repaired, or is rolled back.

## Recommended Use Order

1. Fill `planner_prompt.md` with the current champion, feedback, rulebase, and
   cycle budget.
2. Convert the planner JSON into an assignment under
   `experiments/{{CYCLE_ID}}/agents/assignments/`.
3. Render `coding_agent_prompt.md` for the selected paper role.
4. If compile, smoke, CEC, or runtime fails, render `repair_prompt.md`.
5. If validation completes, render `review_prompt.md`.
6. Store model-derived artifacts under `experiments/{{CYCLE_ID}}/agents/`.

## Output Protocol

The executable scaffold expects model responses as JSON objects. Markdown
reports are generated after schema validation. Prompt templates therefore
describe both the reasoning requirements and the exact JSON keys expected from
the model.

For the current scaffold:

- Planning Agent JSON is consumed by `planning_agent.py`.
- Coding Agent JSON is consumed by `coding_agents/base_coding_agent.py`.
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
- First-cycle prompts should favor reversible flow artifacts and diagnostics
  over source edits.
- Prompt rendering should summarize logs and CSVs; benchmark sources and
  generated outputs should not be copied wholesale into a model call.

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

## First-Cycle Prompt Bundle

For `cycle_001`, provide these evidence files to the Flow Agent:

- `experiments/cycle_000/results/summary.csv`
- `experiments/cycle_000/results/skipped.csv`
- `experiments/cycle_000/results/run_notes.md`
- selected scripts under `experiments/cycle_000/outputs/`
