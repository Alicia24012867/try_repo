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
  --max-cycles 5
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
#   --max-cycles 5             Maximum number of automatic cycles, including
#                              the starting Planning dispatch.
#
#   Other dual_agent_loop options:
#     --timeout-seconds 300    ABC runtime timeout per benchmark, in seconds.
#     --build-timeout-seconds 900  Candidate ABC build timeout, in seconds.
#     --repo-root .            Repository root, defaults to cwd.
