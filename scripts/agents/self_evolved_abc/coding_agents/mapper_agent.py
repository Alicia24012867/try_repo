"""Mapper Agent scaffold."""

from __future__ import annotations

from scripts.agents.self_evolved_abc.coding_agents.base_coding_agent import CodingAgent


class MapperAgent(CodingAgent):
    """Technology mapping agent scaffold."""

    agent_name = "mapper_agent"
    paper_role = "Mapper Agent"
    prompt_template = "configs/agents/prompts/coding_agent_prompt.md"
    allowed_subsystems = (
        "third_party/FlowTune/src/map",
        "third_party/FlowTune/src/map/mapper",
    )
    candidate_kind = "mapping_heuristic_todo"

    def mapping_boundary(self) -> tuple[str, ...]:
        """Return mapper paths this agent may eventually edit.

        TODO_MAPPING_AGENT: add cut-ranking and mapper-cost proposal handling
        only after the first flow-only cycle is stable.
        """

        return self.allowed_subsystems

