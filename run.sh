#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# ----------------------------------------------------------------------------
# Model configuration loaded from .env
# ----------------------------------------------------------------------------
set -a
source .env
set +a

# ----------------------------------------------------------------------------
# Start the multi-cycle autonomous iteration loop.
# ----------------------------------------------------------------------------
# Full ABC compile, CEC, and QoR comparison should run on the remote Linux/ABC
# host. Local macOS runs should stay limited to lightweight Python checks and
# code editing.
#
# cycle_001 assignment is created once by init_cycle.py and tracked in git.
# Later cycle assignments are generated automatically by next_cycle.py.
python3 -B -m scripts.agents.self_evolved_abc.flow.cycle_loop \
  --auto-resume \
  --build-candidate-binary \
  --build-jobs 8 \
  --max-cycles 5
#
# Arguments:
#   --auto-resume              Start from the cycle after the latest completed
#                              review_decision.json without overwriting data.
#                              If no cycle is complete, starts from cycle_001.
#                              For an explicit start point, remove
#                              --auto-resume and pass --assignment instead.
#
#   --build-candidate-binary   Build the candidate ABC binary in S4.
#                              Remove this flag to skip compile and run only
#                              Python smoke checks.
#                              Enable it on the remote Linux/ABC host.
#
#   --build-jobs 8             Number of parallel make jobs.
#
#   --max-cycles 5             Maximum number of automatic cycles, including
#                              the starting cycle.
#                              Each cycle = model call -> patch apply ->
#                              compile -> CEC -> QoR -> review ->
#                              next assignment generation.
#
#   Stop conditions:
#     - --max-cycles reached
#     - same review decision for 3 consecutive cycles
#     - repeated NEEDS_HUMAN_REVIEW validation failures
#     - next assignment does not exist
#
#   Other cycle_loop options:
#     --timeout-seconds 300    ABC runtime timeout per benchmark, in seconds.
#     --build-timeout-seconds 900  Candidate ABC build timeout, in seconds.
#     --repo-root .            Repository root, defaults to cwd.
