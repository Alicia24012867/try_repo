"""Flow Agent scaffold."""

from __future__ import annotations

from pathlib import Path

from scripts.agents.self_evolved_abc.coding_agents.base_coding_agent import CodingAgent


class FlowAgent(CodingAgent):
    """Flow Agent for flow scheduling and FlowTune-related candidates."""

    agent_name = "flow_agent"
    paper_role = "Flow Agent"
    prompt_template = "configs/agents/prompts/coding_agent_prompt.md"
    allowed_subsystems = ("configs/flows", "third_party/FlowTune/src/opt/flowtune")
    candidate_kind = "abc_flow"

    def candidate_flow_path(self) -> Path:
        """Return the first-cycle flow artifact path.

        TODO_FLOW_CANDIDATE: only write this file after the model returns a
        validated list of ABC commands.
        """

        return (
            self.context.repo_root
            / "configs"
            / "flows"
            / f"{self.context.cycle_id}_{self.context.candidate_id}.abc"
        )

