"""Logic Minimization Agent scaffold."""

from __future__ import annotations

from scripts.agents.self_evolved_abc.coding_agents.base_coding_agent import CodingAgent


class LogicMinimizationAgent(CodingAgent):
    """Technology-independent AIG optimization agent scaffold."""

    agent_name = "logic_minimization_agent"
    paper_role = "Logic Minimization Agent"
    prompt_template = "configs/agents/prompts/coding_agent_prompt.md"
    allowed_subsystems = (
        "third_party/FlowTune/src/base/abci",
        "third_party/FlowTune/src/aig",
        "third_party/FlowTune/src/opt",
    )
    candidate_kind = "source_patch_todo"

    def source_patch_boundary(self) -> tuple[str, ...]:
        """Return source paths this agent may eventually edit.

        TODO_SOURCE_PATCH_AGENT: first-cycle scaffold should not modify these
        paths; later cycles must require compile and CEC gates.
        """

        return self.allowed_subsystems

