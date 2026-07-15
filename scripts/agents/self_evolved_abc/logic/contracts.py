"""Paper-aligned contracts for the Logic Minimization Agent.

The paper assigns technology-independent logic optimization exclusively to
``src/base/abci``.  These constants intentionally describe a smaller domain
than the generic Flow source-patch machinery: Logic proposals are unified
diffs over existing ABCI C/header files and are evaluated only after build and
formal-equivalence gates.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
)


LOGIC_AGENT_NAME = "logic_minimization_agent"
LOGIC_PAPER_ROLE = "Logic Minimization Agent"
LOGIC_ABCI_ROOT = "third_party/FlowTune/src/src/base/abci"
LOGIC_SOURCE_PATCH_MODE = FLOW_CANDIDATE_SOURCE_PATCH_DIFF

# The paper reports evolved rewrite, resubstitution, refactoring, and
# orchestration behavior.  ``orchestrate`` is a planning target rather than a
# required command in the older bundled FlowTune fork; its local touchpoint is
# the existing command/orchestration wrapper in abc.c.
LOGIC_REACHABLE_TARGET_COMMANDS = (
    "rewrite",
    "resub",
    "refactor",
    "orchestrate",
)

# This sequence exercises all locally available paper-role pass families.  A
# paired Planning round may replace it with its frozen, shared evaluation
# contract, but it remains the Logic branch's diagnostic recipe.
LOGIC_EVALUATION_FLOW_COMMANDS = (
    "strash",
    "balance",
    "rewrite -z",
    "refactor -z",
    "resub -K 8",
    "rewrite -z",
    "balance",
    "strash",
    "print_stats",
)

LOGIC_SOURCE_TOUCHPOINTS: Mapping[str, tuple[str, ...]] = {
    "rewrite": (
        f"{LOGIC_ABCI_ROOT}/abc.c",
        f"{LOGIC_ABCI_ROOT}/abcRewrite.c",
    ),
    "resub": (
        f"{LOGIC_ABCI_ROOT}/abc.c",
        f"{LOGIC_ABCI_ROOT}/abcResub.c",
    ),
    "refactor": (
        f"{LOGIC_ABCI_ROOT}/abc.c",
        f"{LOGIC_ABCI_ROOT}/abcRefactor.c",
    ),
    "orchestrate": (
        f"{LOGIC_ABCI_ROOT}/abc.c",
        f"{LOGIC_ABCI_ROOT}/abcBalance.c",
        f"{LOGIC_ABCI_ROOT}/abcRewrite.c",
        f"{LOGIC_ABCI_ROOT}/abcRefactor.c",
        f"{LOGIC_ABCI_ROOT}/abcResub.c",
    ),
}

LOGIC_SOURCE_SUFFIXES = (".c", ".h")
LOGIC_FORBIDDEN_BUILD_FILES = frozenset(
    ("Makefile", "module.make", "CMakeLists.txt")
)
LOGIC_GATE_ORDER = ("compile", "cec", "qor")


def logic_source_roots(
    assignment: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return the exact paper-owned write domain.

    ``assignment`` is accepted so callers can share a uniform helper shape
    with other coding roles.  It cannot expand Logic ownership; cross-domain
    edits would violate the paper's non-overlapping source-domain invariant.
    """

    del assignment
    return (LOGIC_ABCI_ROOT,)


def normalize_logic_target_command(value: object) -> str:
    """Return one deterministic, reachable Logic planning target."""

    words = str(value or "").strip().lower().split(maxsplit=1)
    if not words:
        return LOGIC_REACHABLE_TARGET_COMMANDS[0]
    command = words[0]
    aliases = {
        "rewriting": "rewrite",
        "resubstitution": "resub",
        "refactoring": "refactor",
        "orchestration": "orchestrate",
    }
    command = aliases.get(command, command)
    if command not in LOGIC_REACHABLE_TARGET_COMMANDS:
        return LOGIC_REACHABLE_TARGET_COMMANDS[0]
    return command


def logic_touchpoints_for(target_command: object) -> tuple[str, ...]:
    """Return existing ABCI touchpoints for a normalized target command."""

    command = normalize_logic_target_command(target_command)
    return tuple(LOGIC_SOURCE_TOUCHPOINTS[command])
