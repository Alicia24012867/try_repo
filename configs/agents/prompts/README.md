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
prompt to an agent.

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
2. Copy the planner's "Coding-Agent Task" into `coding_agent_prompt.md`.
3. If compile, smoke, CEC, or runtime fails, fill `repair_prompt.md`.
4. If validation passes, fill `review_prompt.md`.
5. Store outputs under `experiments/{{CYCLE_ID}}/agents/`.

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
