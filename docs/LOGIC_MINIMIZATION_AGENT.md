# Logic Minimization Agent

## Outcome

The paper's technology-independent AIG-synthesis role is executable in this
repository. It renders the shared strict coding prompt, reads local FlowTune
entry points, injects pinned related-repository profiles/code excerpts, accepts
one machine-applicable unified diff, materializes that diff as an artifact, and
uses the existing isolated build, CEC-first comparison, QoR review, champion
lineage, and next-cycle feedback loop.

## Paper-to-code mapping

The paper assigns this agent rewrite, refactor, resubstitution, and
orchestration work under `src/base/abci`, with compile and formal equivalence
before QoR. The pinned FlowTune fork exposes reachable rewrite, refactor,
resubstitution, balance, and `dc2` wrappers; it does not contain/register
upstream ABC's `orchestrate` command. Automatic cycles therefore do not invent
an unreachable orchestration target. A future port must be separately approved,
registered, compiled, and added to the shared evaluation recipe. This
implementation maps the executable contract to:

The paper does not publish a verbatim Logic Agent prompt, so this is a
contract-level reconstruction from its stated role, context composition,
source boundary, and compile/CEC/QoR loop rather than a claim of prompt identity.

- `coding_agents/logic_minimization_agent.py`: prompt, local source index,
  minimum knowledge-context gate, and role-specific commands.
- `logic/contracts.py`: exact source ceiling, operation-to-file/symbol map, and
  stable evaluation flow.
- `logic/assignment.py`: scope normalization that removes caller-supplied
  Mapper or other cross-role paths.
- `flow/validation.py` and `flow/source_patch_runner.py`: the same role ceiling
  at model-response validation and isolated patch application time.
- `workflow/candidate_pipeline.py`, `workflow/branch_run.py`, and
  `workflow/dual_agent_loop.py`: isolated branch execution, lineage-bound
  resume, all-settled fan-in, and next-round continuation.

## Source boundary

Default writable source:

```text
third_party/FlowTune/src/src/base/abci
```

The current contract is deliberately exact: only existing `.c`/`.h` files
under `src/base/abci` are legal. `opt/rwr`, `opt/res`, `opt/dar`, Mapper,
sequential, benchmark, prompt, evaluation, previous-cycle, build-metadata, new,
deleted, or renamed paths are rejected. Actual FlowTune source is never edited
during model materialization; the diff is applied only inside the candidate's
isolated `candidate_scoped_v2` workspace.

## Read-only pre-evolution knowledge

This is a direct response to the paper's context requirement, not a generic
retrieval add-on. Figure 1 and Section 3.1 call for ABC and related-repository
profiling/code-indexing; Section 3.3 supplies that profile and a structured
Markdown tutorial in cycle 0. Section 4.2 reports 68% of token use for ABC
profiling and another 11% for external codebase profiling.

`configs/agents/context/repositories.json` pins ten codebases and ranks them for
quality, extensibility, self-evolution synergy, and ABC integration. Nine are
external, read-only Logic references; FlowTune is verified at an exact commit
but supplied through the separate local source index because it is the actual
assignment-controlled build source:

| Repository | Primary lesson | Integration caveat |
| --- | --- | --- |
| Berkeley ABC | Native wrappers, algorithms, orchestration, module registration | Newer APIs have ABI drift; local FlowTune signatures win |
| FlowTune (local source index, not external prompt) | Exact build source, command reachability, tuning precedent | Research subprocess/file patterns are not safe algorithm code |
| mockturtle | Parameter/statistics contracts and bounded local transforms | Generic C++ APIs are not ABC C APIs |
| LSOracle | Multi-pass schedules and adaptive selection | Research orchestration, foreign dependencies, and submodules |
| Yosys | Robust ABC invocation, scripts, diagnostics, cleanup | RTLIL/mapping/retiming semantics are outside this role |
| OpenROAD-flow-scripts | Reproducible stages and metric reporting | Physical-design PPA is not AIG equivalence/QoR |
| kitty | Truth tables, care sets, decomposition, ISOP/ESOP, NPN | C++ truth-table APIs are not ABC's C data structures |
| alice | Command registry, state handoff, histories, structured logs | Do not add a second shell or transplant its C++ DSL |
| CUDD | Cofactor/decomposition, caching, ownership, consistency | BDD nodes and reference counting are not ABC AIG APIs |
| EQY | Proof partitions, strategies, job status, failure provenance | Workflow precedent only; ABC CEC remains the hard gate |

The large checkouts live in ignored `.local/context_repos/` paths; the manifest,
commit hashes, licenses, focus paths, query terms, and profiles are tracked.
Prompt construction requires an exact HEAD, complete focus paths, and a clean
checkout before scanning code. It ranks files against the current target,
round-robins snippets across the nine external repositories, and enforces a hard
character budget. The Logic default is 72,000 characters and three source files per
repository, bounded to 2,000–160,000 characters and one to ten files. Large
files contribute at most three query-centered windows. Dirty, incomplete,
missing, or revision-mismatched trees fall back to checked-in profiles and
cannot inject source excerpts. External paths are never merged into
`allowed_to_edit`.

Provision and verify:

```bash
python3 -B scripts/bootstrap_agent_context.py
python3 -B scripts/bootstrap_agent_context.py --check
```

Bootstrap verification requires all ten pinned trees. Logic prompt construction
then requires all nine external repositories at exact clean revisions; otherwise it
fails with a reproducible bootstrap instruction. FlowTune source remains local,
indexed under the role's writable ceiling, and is never mislabeled as a
read-only external repository.

The generated context header records configured/available counts, the requested
minimum, whether it is satisfied, budget, per-repository file count, missing
trees, revision mismatches, incomplete focus paths, and dirty trees. This keeps
profile-only degradation visible to the model and auditable by the planner.

## Candidate and gate contract

One candidate must trace an evaluation command through `abc.c`, its wrapper,
network conversion, and the changed decision. It may change one reached
default, score/tie-break, bounded threshold, stopping rule, or conservative
orchestration of existing passes. It must not add retiming/sequential behavior,
bypass legality checks, branch on benchmark names, or transplant upstream code
without checking local APIs and ownership.

Gate order is fixed:

1. Strict JSON, unified-diff, declared-target, and role-scope validation.
2. Strictly apply-check the diff in a disposable copy of the exact frozen
   `baseline_ref.source_root`. A context mismatch re-enters the bounded Coding
   repair loop with the target file promoted into key source context.
3. Apply the diff only in the isolated candidate workspace and compile ABC.
4. Run a command smoke on the shared evaluation recipe.
5. Run CEC/`dsat` for every evaluated design.
6. Only CEC-backed rows may contribute AIG node/depth/runtime QoR.
7. Review accepts, repairs, rejects, or rolls back; accepted source becomes the
   next champion lineage, while concise evaluated lessons become next-cycle
   `evolved_rules`.

A Python-smoke-only status cannot enter step 5. Source-patch comparison requires
`candidate_binary_build_passed`, and a candidate binary resolving to the
baseline binary is rejected as self-comparison.

Prompt source and patch application resolve the same authoritative
`baseline_ref.source_root`; a missing snapshot or alias drift fails closed and
never falls back to the live vanilla tree. Patch application uses strict
`git apply` semantics without fuzzy hunks or whitespace relaxation.

## Local verification and Linux handoff

```bash
PYTHONPATH=. python3 -B scripts/test_logic_minimization_agent.py

python3 -B scripts/init_cycle.py cycle_006 \
  --previous-cycle cycle_005 \
  --agent-name logic_minimization_agent \
  --source-patch-mode source_patch_diff

# On the Linux/ABC host, run the full compile -> CEC -> QoR loop:
bash run.sh
```

macOS can validate scopes, prompt rendering, JSON, diff materialization, and
Python orchestration. The bundled ABC executable is Linux-format, so candidate
C compilation, CEC, and QoR remain Linux/ABC-host gates; they must be run before
claiming a logic-quality improvement.
