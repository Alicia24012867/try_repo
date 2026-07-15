# Pinned Repository Context

This directory implements the paper's cycle-0 repository profiling and
code-indexing input without vendoring large third-party histories into this
project. Section 3.1 and Figure 1 describe profiling ABC and related
repositories before evolution; Section 3.3 describes feeding a repository-wide
profile and structured Markdown guidance to the planner. Section 4.2 reports
that 68% of token use went to the ABC profile and another 11% to external
codebase profiling, so this knowledge layer is a required input rather than an
optional prompt decoration.

## Inventory

`repositories.json` uses fail-closed schema version 2, pins exact 40-hex
commits, and records quality,
extensibility, self-evolution synergy, ABC integration, licenses, focus paths,
query terms, role routing, and checkout mode.

| Repository | Pinned commit | Prompt use |
| --- | --- | --- |
| Berkeley ABC | `bcfdf592289a` | Native wrappers, algorithms, orchestration, build registration |
| FlowTune | `19d95ed6e25f` | Verified local build source and command-reachability authority |
| mockturtle | `25beb0e294e4` | Bounded transform parameters, statistics, callbacks, deterministic fallbacks |
| LSOracle | `a0e921318be3` | Multi-pass AIG schedules and adaptive selection precedents |
| Yosys | `d4f39588a732` | Robust ABC invocation, scripts, diagnostics, and cleanup |
| OpenROAD-flow-scripts | `bea7dcd7be7f` | Reproducible stages, metrics, and failure-visible evaluation |
| kitty | `61ec6bd2a197` | Truth tables, care sets, decomposition, ISOP/ESOP, NPN canonicalization |
| alice | `8881c2c0a728` | Command registration, typed state, histories, and structured logging |
| CUDD | `f54f53330364` | Cofactor/composition, BDD discipline, caching, ownership, consistency checks |
| EQY | `fe7163db01bc` | Equivalence partitions, proof strategies, all-settled job status, failures |

FlowTune remains tagged `build_source` and is the authoritative local API/build
tree. It is also routed to the Planning Agent's cycle-0 profile, but it is not
duplicated in either Coding Agent's external-reference section. The other nine
repositories can contribute read-only Logic snippets; Flow routing selects six
repositories whose command, scheduling, metrics, and proof-lifecycle patterns
are relevant to that role.

## Budget and selection

`repository_context.py` provides deterministic prompt construction:

- role hard budgets: Planning 96,000 characters, Logic 72,000, Flow 60,000;
  assignment bounds remain 2,000–160,000;
- default source selection: three files per trusted repository; bound: 1–10;
- role filter and priority ordering before repository-count truncation;
- query terms derived from the target command, hypothesis, subsystem, role,
  and repository-specific vocabulary;
- ranked files with round-robin snippet emission, so one large repository
  cannot consume the full code budget;
- at most three local windows for a large file, with explicit truncation
  markers when the budget is exhausted.

Planning requires all ten pinned repositories, including the local FlowTune
build source. Logic requires all nine external references, while Flow requires
its six role-routed references. Each assignment sets
`repository_context_enforce_minimum: true`; the bootstrap manifest sets
`minimum_available: 10` so preflight verifies the complete cycle-0 prior.

## Trust and failure policy

Only an exact, clean checkout with every focus path present can contribute
source text. A checked-in profile is also mandatory; a valid checkout without
its profile does not count toward the role minimum and cannot contribute code.
Profiles are tracked and remain available for an explicit
profile-only degradation, but untrusted source is never scanned.

| Condition | Prompt behavior | Bootstrap `--check` |
| --- | --- | --- |
| Exact revision, clean, complete | Profile plus ranked source excerpts | `READY` |
| Missing checkout | Profile-only fallback | `MISSING`, failure below minimum |
| Wrong revision | Profile-only fallback | `REVISION`, failure |
| Missing focus path | Profile-only fallback | `INCOMPLETE`, failure |
| Dirty checkout | Profile-only fallback; untracked text cannot enter prompt | `DIRTY`, failure |
| Missing checked-in profile | Clear no-profile card | `PROFILE_MISSING`, failure |
| Manifest or checkout path escapes the project | Context disabled or manifest rejected | manifest error |

Repository prose and code comments are untrusted reference data. They cannot
override the coding prompt, enter `allowed_to_edit`, appear as a diff target,
or relax compile, CEC, QoR, and role-scope gates. Ideas must be translated into
the pinned local FlowTune API and validated independently.

## Provision and verify

```bash
# Fetch sparse/full checkouts at the exact manifest commits.
python3 -B scripts/bootstrap_agent_context.py

# Read-only verification; suitable for CI and preflight checks.
python3 -B scripts/bootstrap_agent_context.py --check

# Inspect or refresh one named repository.
python3 -B scripts/bootstrap_agent_context.py --check --only berkeley-abc
python3 -B scripts/bootstrap_agent_context.py --refresh --only berkeley-abc
```

Provisioning refuses to overwrite a dirty checkout or a non-empty non-Git
directory. Reference repositories live under ignored `.local/context_repos/`;
the FlowTune entry verifies the checked-in `third_party/FlowTune` source tree.
