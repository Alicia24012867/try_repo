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

- Champion lineage is centralized in `flow/lineage.py`, so source context,
  isolated workspaces, and baseline binary selection use the same assignment
  fields.
- Promotion thresholds are centralized in `flow/promotion.py`, so review,
  prompts, and initial/next assignments agree on what counts as a champion.
- Weak one-row or one-node improvements are treated as repair feedback rather
  than accumulated into the champion lineage.
- Deterministic batch search is model-free and still passes through S4/S5/review;
  it increases feedback density without weakening correctness gates.
- Duplicate `* 2.*` backup files were removed because they were unreferenced and
  could pollute source search, prompt context, and validation reasoning.

## Remaining Remote Evidence Needed

- Candidate binary build logs from the server.
- CEC summary for every evaluated benchmark.
- Correctness-backed QoR delta table against the declared champion.
- Batch `summary.csv` and `winner.json` when low-API search is used.
