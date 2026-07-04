# ABC Programming Guidance

This guidance is the compact programming tutorial supplied to coding agents
before they propose source changes. For the first LLM-assisted flow cycle,
agents should use it as context only; source edits remain disabled.

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

## First-Cycle Rule

The first LLM-assisted cycle may generate a flow file and markdown artifacts,
but it must not modify `third_party/FlowTune/src/`.

