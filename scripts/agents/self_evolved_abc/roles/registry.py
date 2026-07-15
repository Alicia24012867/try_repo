"""Canonical registry for paper roles.

Keeping role metadata here prevents the CLI driver, assignment initialiser, and
workflow loops from growing separate dispatch tables.  Imports of concrete
agent classes are lazy so scope normalisation can be used without constructing
model-facing agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Type

from scripts.agents.self_evolved_abc.flow.assignment import (
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.logic.assignment import (
    normalize_logic_assignment_scope,
)


AssignmentNormalizer = Callable[[Mapping[str, Any]], Dict[str, object]]


@dataclass(frozen=True)
class AgentSpec:
    """Stable identity and dispatch metadata for one paper agent."""

    name: str
    paper_role: str
    module: str
    class_name: str
    coding_agent: bool = False
    candidate_prefix: str = "candidate"
    normalizer: Optional[AssignmentNormalizer] = None


_AGENT_SPECS = {
    "planning_agent": AgentSpec(
        name="planning_agent",
        paper_role="Planning Agent",
        module="scripts.agents.self_evolved_abc.planning_agent",
        class_name="PlanningAgent",
    ),
    "flow_agent": AgentSpec(
        name="flow_agent",
        paper_role="Flow Agent",
        module="scripts.agents.self_evolved_abc.coding_agents.flow_agent",
        class_name="FlowAgent",
        coding_agent=True,
        candidate_prefix="flow_candidate",
        normalizer=normalize_flow_assignment_scope,
    ),
    "logic_minimization_agent": AgentSpec(
        name="logic_minimization_agent",
        paper_role="Logic Minimization Agent",
        module=(
            "scripts.agents.self_evolved_abc.coding_agents."
            "logic_minimization_agent"
        ),
        class_name="LogicMinimizationAgent",
        coding_agent=True,
        candidate_prefix="logic_candidate",
        normalizer=normalize_logic_assignment_scope,
    ),
    "mapper_agent": AgentSpec(
        name="mapper_agent",
        paper_role="Mapper Agent",
        module="scripts.agents.self_evolved_abc.coding_agents.mapper_agent",
        class_name="MapperAgent",
        candidate_prefix="mapper_candidate",
    ),
}


def agent_names() -> Tuple[str, ...]:
    """Return all registered agent names in deterministic order."""

    return tuple(sorted(_AGENT_SPECS))


def coding_agent_names() -> Tuple[str, ...]:
    """Return agents supported by the source-patch candidate pipeline."""

    return tuple(
        sorted(name for name, spec in _AGENT_SPECS.items() if spec.coding_agent)
    )


def get_agent_spec(name: object) -> AgentSpec:
    """Return a registered role or fail closed for unknown input."""

    key = str(name or "").strip()
    try:
        return _AGENT_SPECS[key]
    except KeyError as exc:
        raise ValueError(f"unknown agent role: {key or 'missing agent_name'!r}") from exc


def get_coding_agent_spec(name: object) -> AgentSpec:
    """Return a source-patch role, rejecting non-coding agents."""

    spec = get_agent_spec(name)
    if not spec.coding_agent or spec.normalizer is None:
        raise ValueError(
            "source-patch workflow requires a registered coding agent; "
            f"found {spec.name!r}"
        )
    return spec


def normalize_coding_assignment(
    assignment: Mapping[str, Any],
) -> Dict[str, object]:
    """Normalise an assignment according to its exact registered role."""

    spec = get_coding_agent_spec(assignment.get("agent_name"))
    assert spec.normalizer is not None
    normalized = spec.normalizer(assignment)
    if str(normalized.get("agent_name", "")).strip() != spec.name:
        raise ValueError(
            f"{spec.name} normalizer changed assignment role unexpectedly"
        )
    if str(normalized.get("paper_role", "")).strip() != spec.paper_role:
        raise ValueError(
            f"assignment paper_role does not match {spec.name}: "
            f"{normalized.get('paper_role')!r}"
        )
    return normalized


def resolve_agent_class(name: object) -> Type[Any]:
    """Load the registered concrete class only when execution needs it."""

    spec = get_agent_spec(name)
    module = import_module(spec.module)
    agent_cls = getattr(module, spec.class_name)
    if getattr(agent_cls, "agent_name", None) != spec.name:
        raise RuntimeError(f"registered class identity mismatch for {spec.name}")
    if getattr(agent_cls, "paper_role", None) != spec.paper_role:
        raise RuntimeError(f"registered class paper role mismatch for {spec.name}")
    return agent_cls
