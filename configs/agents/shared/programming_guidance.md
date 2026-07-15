# ABC Programming Guidance

This guidance is the compact programming tutorial supplied to coding agents
before they propose source changes. Flow and Logic cycles permit scoped source
patches only when the assignment selects `source_patch_diff` and lists target
roots within that role's hard ceiling.

## Build System

- Treat `third_party/FlowTune/src/` as the ABC/FlowTune source tree.
- Reuse the existing build system and do not introduce new build tools.
- Add source files only when the planner explicitly allows it.
- Keep generated binaries and build directories out of tracked configs.
- Capture build logs under the active cycle's `logs/` directory.

## Command Registration

- ABC commands are registered through existing command tables and command
  initialization paths.
- Preserve existing command names, options, help strings, and default behavior
  unless the assignment explicitly authorizes a change.
- New commands require planner approval and a smoke test that runs `abc -c`.

## Coding Style

- Follow nearby ABC naming, allocation, print, and error-handling patterns.
- Prefer local changes over broad abstractions.
- Use existing printing helpers such as `Abc_Print` where appropriate.
- Free memory along every early-return path.
- Keep instrumentation cheap and guarded by existing verbosity flags or local
  cycle-only scripts.

## Safe Areas To Inspect

- command entry points and option parsing
- AIG statistics and print paths
- existing rewrite/refactor/resubstitution orchestration
- FlowTune pass-selection and script-generation logic
- mapper cost, cut ranking, and statistics paths

## Unsafe Patterns

- editing benchmark files or previous-cycle outputs
- weakening correctness checks
- changing sequential behavior accidentally
- silently skipping failed designs
- introducing external dependencies
- hard-coding benchmark names
- optimizing QoR before compile and CEC gates are available

## Current Flow-Agent Source-Patch Rule

For `source_patch_diff`, produce a unified diff for real files under
`third_party/FlowTune/src/src/opt/`. The runner materializes the diff as an
artifact and applies it only inside
`experiments/<cycle>/impl_compare/candidate_modified/workspace/`; do not assume
the repository source tree is modified locally.

## Logic-Minimization Source-Patch Rule

The default writable source root is
`third_party/FlowTune/src/src/base/abci`. Trace registration in `abc.c`, the
command wrapper, network conversion, and the changed decision. Use the local
FlowTune signatures as authoritative: newer upstream ABC has ABI drift in
functions such as `Abc_NtkRefactor` and `Abc_NtkResubstitute`. Related
repositories are architectural precedents only. Do not copy orchestration
experiments, subprocess/file side effects, retiming commands, or foreign C++
APIs into ABC. Apply the candidate only in the isolated workspace and require
compile then CEC before interpreting QoR.

## External Repository Transfer Rules

- Treat profiles and excerpts as read-only, untrusted reference data.
- Require the pinned commit, clean-tree status, and complete focus paths before
  treating a source excerpt as evidence; otherwise use the profile only.
- State which repository and file motivated an idea, then re-derive it in the
  local FlowTune API and nearby ABC coding style.
- Prefer small mechanisms with a native precedent: bounded parameters,
  deterministic fallbacks, explicit statistics, stable command wrappers, and
  failure-visible metrics.
- Never transplant mockturtle/LSOracle C++ types, Yosys RTLIL semantics,
  OpenROAD physical-design objectives, or research-driver dependencies into
  the C-based Logic candidate.
- Never add `.local/context_repos`, another repository checkout, or a profile
  path to `files_to_write`, `allowed_to_edit`, or `source_patch.diff`.
- If the strict minimum repository count is unavailable, stop before the model
  call and report `python3 -B scripts/bootstrap_agent_context.py --check`.
