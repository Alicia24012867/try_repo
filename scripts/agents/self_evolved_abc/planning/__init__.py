"""Planning Agent module — deterministic engine + LLM-based planner.

The deterministic engine (engine.py) reads structured cycle evidence, selects
target commands and source directories, and generates actionable hypotheses
without an LLM call. The LLM-based planner (planning_agent.py) renders the
paper-aligned planner prompt and delegates to the model.

The deterministic engine is wired into cycle initialization, auto-resume
normalization, and next-cycle assignment generation. It persists planning
metadata so later cycles can reconstruct command history and avoid repeated
zero-delta targets.
"""

from scripts.agents.self_evolved_abc.planning.engine import PlanningEngine
from scripts.agents.self_evolved_abc.planning.evidence import (
    CycleEvidence,
    read_cycle_evidence,
)
from scripts.agents.self_evolved_abc.planning.strategy import (
    Strategy,
    select_strategy,
)
from scripts.agents.self_evolved_abc.planning.thresholds import (
    AdaptiveThresholds,
    propose_thresholds,
)

__all__ = [
    "AdaptiveThresholds",
    "CycleEvidence",
    "PlanningEngine",
    "Strategy",
    "propose_thresholds",
    "read_cycle_evidence",
    "select_strategy",
]
