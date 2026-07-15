"""Role metadata and fail-closed agent dispatch."""

from scripts.agents.self_evolved_abc.roles.registry import (
    AgentSpec,
    agent_names,
    coding_agent_names,
    get_agent_spec,
    get_coding_agent_spec,
    normalize_coding_assignment,
    resolve_agent_class,
)

__all__ = [
    "AgentSpec",
    "agent_names",
    "coding_agent_names",
    "get_agent_spec",
    "get_coding_agent_spec",
    "normalize_coding_assignment",
    "resolve_agent_class",
]
