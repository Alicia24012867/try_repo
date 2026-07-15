"""Role-neutral execution and orchestration for self-evolved ABC.

The canonical CLI is ``python -m
scripts.agents.self_evolved_abc.workflow.dual_agent_loop``.
"""

from scripts.agents.self_evolved_abc.workflow.artifacts import (
    CANDIDATE_SCOPED_LAYOUT,
    implementation_root,
    review_decision_path,
)

__all__ = [
    "CANDIDATE_SCOPED_LAYOUT",
    "implementation_root",
    "review_decision_path",
]
