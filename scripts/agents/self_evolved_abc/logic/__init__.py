"""Logic Minimization Agent contracts, scope normalization, and validation."""

from scripts.agents.self_evolved_abc.logic.assignment import (
    build_logic_allowed_to_edit,
    normalize_logic_assignment_scope,
)
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_ABCI_ROOT,
    LOGIC_AGENT_NAME,
    LOGIC_PAPER_ROLE,
)

__all__ = [
    "LOGIC_ABCI_ROOT",
    "LOGIC_AGENT_NAME",
    "LOGIC_PAPER_ROLE",
    "build_logic_allowed_to_edit",
    "normalize_logic_assignment_scope",
]
