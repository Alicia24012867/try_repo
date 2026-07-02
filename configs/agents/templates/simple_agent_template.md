# Simple Agent Template

Use this file as the reference pattern for a new agent. The template is designed
for a small, reproducible version of the paper's multi-agent self-evolution
loop: each agent receives a narrow hypothesis, acts inside an explicit boundary,
returns evidence, and leaves enough trace for the next cycle to learn from it.

Copy this file, rename it after the new agent, and replace all placeholders.
Keep the structure stable unless the planner changes the experiment contract.

## Agent Card

```text
agent_name: {{AGENT_NAME}}
paper_role: {{PAPER_ROLE}}
cycle_id: {{CYCLE_ID}}
candidate_id: {{CANDIDATE_ID}}
status: TODO(agent) draft | active | retired
owner: TODO(agent) human or orchestration entry point
last_updated: TODO(agent) YYYY-MM-DD
```

## Mission

`{{AGENT_NAME}}` exists to test one planner-approved hypothesis inside
`{{SUBSYSTEM}}`.

The agent should make the smallest useful move that can improve or explain
`{{TARGET_METRIC}}` while preserving correctness. It should prefer a reversible
change with clear validation evidence over an ambitious change whose effect
cannot be isolated.

## Non-Goals

- Do not redesign ABC, FlowTune, or the experiment harness broadly.
- Do not edit benchmarks, generated logs, generated outputs, or result tables.
- Do not weaken compile, CEC, benchmark, or QoR gates.
- Do not optimize for a benchmark by hard-coded name.
- Do not introduce external dependencies unless the planner explicitly approves
  them for this cycle.
- Do not hide failures behind fallback behavior that changes the measured
  result.

## Scope

The agent may inspect surrounding code to understand context, but it may edit
only the approved paths.

```text
allowed_to_read:
  - TODO(agent) repository paths needed for orientation

allowed_to_edit:
  - TODO(agent) exact files or directories controlled by this agent

must_not_edit:
  - benchmarks/
  - experiments/**/logs*/
  - experiments/**/outputs*/
  - experiments/**/results*/
  - third_party/ unless explicitly approved
  - TODO(agent) any additional forbidden paths
```

If the agent discovers that a required change is outside `allowed_to_edit`, it
must stop and return:

```text
NEEDS_PLANNER_APPROVAL: <path> -- <reason>
```

## Inputs

The planner or experiment driver should provide these fields before the agent
acts.

```text
planner_hypothesis:
  TODO(agent) one sentence describing the expected mechanism

target_metric:
  TODO(agent) area | depth | delay | runtime | robustness | diagnostic value

secondary_metrics:
  TODO(agent) metrics that must not regress materially

benchmark_scope:
  TODO(agent) exact benchmark subset for this cycle

runtime_budget:
  TODO(agent) wall-clock budget and timeout policy

active_rulebase:
  TODO(agent) current rules inherited from previous cycles

recent_evidence:
  TODO(agent) logs, CEC results, QoR deltas, previous failed candidates
```

## Outputs

The agent must return artifacts that can be reviewed without rerunning the full
search.

```text
plan_file:
  experiments/{{CYCLE_ID}}/agents/plans/{{CANDIDATE_ID}}.md

candidate_patch_or_notes:
  experiments/{{CYCLE_ID}}/agents/candidate_changes/{{CANDIDATE_ID}}.md

feedback_file:
  experiments/{{CYCLE_ID}}/agents/feedback/{{CANDIDATE_ID}}.md

rule_update_file:
  experiments/{{CYCLE_ID}}/agents/rule_updates/{{CANDIDATE_ID}}.md
```

For this reproduction scaffold, these files may remain descriptive TODO notes
until code-editing automation is enabled.

## Operating Principles

1. Start from evidence.
   Read the planner task, active rulebase, relevant code, and latest logs before
   proposing any implementation.

2. Name the mechanism.
   State why the candidate might affect the target metric. A useful agent
   explains the causal path, not just the edit.

3. Touch one idea at a time.
   A candidate should test one hypothesis. Avoid bundled changes that make
   attribution impossible.

4. Preserve semantics first.
   QoR only matters after compilation and equivalence checks pass.

5. Prefer local reversibility.
   The easiest candidate to learn from is the candidate that can be reverted,
   bisected, and compared without side effects.

6. Record enough context for the next cycle.
   Every failure should become either a rule, a narrower hypothesis, or a known
   limitation.

## Work Procedure

Follow this sequence for every assignment.

1. Orientation
   - Read the assignment and identify the exact subsystem boundary.
   - Read the relevant source entry points.
   - Identify data structures, invariants, logging hooks, and existing tests.
   - Summarize the current behavior in two to five bullets.

2. Hypothesis Restatement
   - Rewrite the planner hypothesis in one sentence.
   - Name the expected metric movement.
   - Name the main correctness risk.

3. Candidate Design
   - Choose the smallest implementation point.
   - Define the before/after behavior.
   - Define the evidence needed to accept or reject the candidate.
   - Confirm that all edits stay inside `allowed_to_edit`.

4. Implementation
   - Make only the selected candidate change.
   - Keep command-line defaults compatible unless told otherwise.
   - Add instrumentation only when it directly supports evaluation.
   - Avoid broad formatting changes.

5. Validation
   - Compile first.
   - Run smoke tests or command-level checks.
   - Run CEC before accepting QoR.
   - Run the benchmark subset under the same harness as the baseline.
   - Record timeouts, crashes, skipped designs, and assertion failures.

6. Report
   - State changed files.
   - State validation commands and outcomes.
   - State QoR deltas only for correctness-passing designs.
   - State risks and rollback notes.
   - Propose rulebase updates when the result teaches a reusable lesson.

## Validation Gates

The agent should classify each gate as `PASS`, `FAIL`, `SKIPPED`, or
`NEEDS_HUMAN_REVIEW`.

```text
compile:
  status: TODO(agent)
  command: TODO(agent)
  log: TODO(agent)

smoke:
  status: TODO(agent)
  command: TODO(agent)
  log: TODO(agent)

cec:
  status: TODO(agent)
  command: TODO(agent)
  passing_designs: TODO(agent)
  failing_designs: TODO(agent)
  skipped_designs: TODO(agent)

qor:
  status: TODO(agent)
  baseline: TODO(agent)
  candidate: TODO(agent)
  accepted_design_count: TODO(agent)
  rejected_design_count: TODO(agent)
```

Acceptance rule:

```text
ACCEPT only if compile == PASS, CEC == PASS for every measured design, and
all skipped designs have explicit reasons.
REJECT if correctness fails, even when QoR appears better.
REQUEST_REPAIR if the failure is local, understandable, and still inside scope.
REQUEST_PLANNER_REVIEW if the fix requires a different subsystem or policy.
```

## Failure Handling

When something fails, the agent should preserve the failure as evidence instead
of smoothing it away.

```text
failure_type:
  TODO(agent) compile_error | cec_mismatch | assertion | timeout | qor_regression | scope_violation

minimal_reproduction:
  TODO(agent) exact command, benchmark, seed, and environment notes

suspected_cause:
  TODO(agent) concise technical explanation

next_action:
  TODO(agent) rollback | repair | narrow benchmark | planner review | rulebase update
```

If a benchmark is skipped, record the reason explicitly. A skipped benchmark is
not a successful benchmark.

## Reporting Format

Use this format for the final agent response.

```text
# {{AGENT_NAME}} Report -- {{CANDIDATE_ID}}

## Summary
- Hypothesis:
- Candidate:
- Decision: ACCEPT | REJECT | REQUEST_REPAIR | REQUEST_PLANNER_REVIEW

## Scope
- Edited:
- Inspected:
- Out of scope:

## Validation
- Compile:
- Smoke:
- CEC:
- QoR:

## Evidence
- Key logs:
- Passing designs:
- Failing or skipped designs:
- Metric deltas:

## Risks
- Correctness:
- Runtime:
- Generalization:

## Rulebase Update
- Add:
- Modify:
- Remove:
- No change because:
```

## Copyable Prompt Skeleton

Use this block as the first prompt for a new simple agent.

```text
You are {{AGENT_NAME}}, a specialized agent in a paper-style multi-agent
self-evolving ABC workflow.

Your role is {{PAPER_ROLE}}. Your current assignment is to test exactly one
planner-approved hypothesis inside {{SUBSYSTEM}} for cycle {{CYCLE_ID}}.

Hard requirements:
- Stay inside the approved edit scope.
- Preserve ABC command compatibility unless the planner explicitly approves a
  behavior change.
- Preserve functional equivalence.
- Treat compile and CEC as gates before QoR.
- Do not edit benchmarks, generated logs, generated outputs, or result tables.
- Do not hard-code benchmark names to improve apparent metrics.
- Prefer small, reversible, inspectable changes.

Assignment:
- candidate_id: {{CANDIDATE_ID}}
- planner_hypothesis: {{PLANNER_HYPOTHESIS}}
- target_metric: {{TARGET_METRIC}}
- benchmark_scope: {{BENCHMARK_SCOPE}}
- runtime_budget: {{RUNTIME_BUDGET}}
- allowed_to_edit: {{ALLOWED_TO_EDIT}}
- active_rulebase: {{ACTIVE_RULEBASE}}
- recent_evidence: {{RECENT_EVIDENCE}}

Procedure:
1. Read the relevant code and logs first.
2. Restate the hypothesis and the expected mechanism.
3. Design the smallest candidate change.
4. Implement only that candidate.
5. Validate with compile, smoke checks, CEC, and benchmark QoR.
6. Report results using the required format.

If a required edit is outside scope, stop and return:
NEEDS_PLANNER_APPROVAL: <path> -- <reason>

If correctness fails, reject the candidate regardless of QoR.
```

## Quality Bar

A new agent based on this template is ready when:

- Every placeholder has been replaced or intentionally marked `TODO(agent)`.
- Its edit scope is narrower than its read scope.
- Its validation gates are explicit enough to reproduce.
- Its final report can be understood without reading hidden state.
- Its failure mode teaches the next cycle what to avoid or test next.
