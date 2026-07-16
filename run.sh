#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# ----------------------------------------------------------------------------
# Model configuration loaded from .env
# ----------------------------------------------------------------------------
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -B -c '
import sys
minimum = (3, 8)
current = sys.version_info[:2]
if current < minimum:
    raise SystemExit(
        "run.sh requires Python >= 3.8; found {}.{}".format(*current)
    )
print("python runtime ready: {}".format(sys.version.split()[0]))
'

# Source-patch JSON responses include code context, validation plans, and a
# unified diff. Use a larger default while preserving provider-specific values
# explicitly configured in .env.
export EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS="${EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS:-16384}"

# Bound only newly advanced evaluation cycles in this invocation. Completed,
# lineage-valid history is fast-forwarded without consuming this budget. A
# budget stop prepares the next dispatch; the absolute target stop does not.
EDA_AGENT_NEW_CYCLE_BUDGET="${EDA_AGENT_NEW_CYCLE_BUDGET:-10}"
EDA_AGENT_TARGET_CYCLE="${EDA_AGENT_TARGET_CYCLE:-10}"

# The paper front-loads repository profiling before cycle 0.  Fail before any
# model call if a pinned prior-knowledge checkout is absent, dirty, incomplete,
# or at the wrong revision.
"$PYTHON_BIN" -B scripts/bootstrap_agent_context.py --check

# ----------------------------------------------------------------------------
# Start the Planning -> (Flow || Logic) -> review loop.
# ----------------------------------------------------------------------------
# Full ABC compile, CEC, and QoR comparison should run on the remote Linux/ABC
# host. Local macOS runs should stay limited to lightweight Python checks and
# code editing.
#
# Planning creates a frozen two-branch portfolio. Flow and Logic execute in
# separate candidate lanes, are reviewed independently, and fan in before the
# next Planning round is allowed to start.
"$PYTHON_BIN" -B -m scripts.agents.self_evolved_abc.workflow.dual_agent_loop \
  --cycle-id cycle_001 \
  --previous-cycle cycle_000 \
  --max-workers 2 \
  --build-candidate-binary \
  --build-jobs 8 \
  --new-cycle-budget "$EDA_AGENT_NEW_CYCLE_BUDGET" \
  --target-cycle "$EDA_AGENT_TARGET_CYCLE"
#
# Arguments:
#   --max-workers 2            Run Flow and Logic concurrently. Use 1 for a
#                              deterministic serial fallback.
#
#   --build-candidate-binary   Build the candidate ABC binary in S4.
#                              Source-patch branches cannot enter CEC/QoR with
#                              only a Python smoke result; keep this enabled on
#                              the remote Linux/ABC host.
#
#   --build-jobs 8             Number of parallel make jobs.
#
#   --new-cycle-budget N       Maximum number of unfinished cycles advanced by
#                              this invocation. Valid completed history is
#                              fast-forwarded for free. The Nth review is fed
#                              into a prepared N+1 Planning dispatch, whose
#                              candidates remain unexecuted until the next run,
#                              unless N reaches --target-cycle.
#                              Set EDA_AGENT_NEW_CYCLE_BUDGET to configure it;
#                              the run.sh safety default is 10.
#
#   --target-cycle N           Absolute final cycle to execute. The default is
#                              cycle 10, so resuming after cycle 5 executes
#                              cycles 6-10 and stops after cycle 10's review
#                              without preparing an unused cycle 11 dispatch.
#                              The terminal champion is written to
#                              planning/final_champion.json; exit is nonzero if
#                              no correctness-backed champion exists.
#
#   Other dual_agent_loop options:
#     --timeout-seconds 300    ABC runtime timeout per benchmark, in seconds.
#     --build-timeout-seconds 900  Candidate ABC build timeout, in seconds.
#     --repo-root .            Repository root, defaults to cwd.
