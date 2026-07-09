"""Planning Agent scaffold."""

from __future__ import annotations

from typing import Any, Mapping

from scripts.agents.self_evolved_abc.base_agent import PaperAgent
from scripts.agents.self_evolved_abc.model_client import ModelInvocation, ModelReply
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts, markdown_bullets


class PlanningAgent(PaperAgent):
    """Paper-style Planning Agent.

    Owns cycle objectives, subsystem selection, rollback policy, and global QoR
    interpretation. This scaffold does not yet execute child agents.
    """

    agent_name = "planning_agent"
    paper_role = "Planning Agent"
    prompt_template = "configs/agents/prompts/planner_prompt.md"

    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        system_prompt = (
            "You are the Planning Agent for a small reproduction of "
            "Multi-Agent Self-Evolved ABC. Propose one conservative next-cycle "
            "objective and select which coding agent should act."
        )
        user_prompt = (
            "TODO_PLANNER_PROMPT_RENDER: render planner_prompt.md with cycle "
            "evidence, rulebase, benchmark scope, and current budget.\n\n"
            f"Evidence files: {', '.join(evidence.keys())}"
        )
        return ModelInvocation(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=self.response_schema(),
        )

    def response_schema(self) -> Mapping[str, Any]:
        return {
            "type": "object",
            "required": [
                "cycle_objective",
                "selected_agent",
                "candidate_id",
                "benchmark_scope",
                "risk_controls",
            ],
            "properties": {
                "cycle_objective": {"type": "string"},
                "selected_agent": {
                    "type": "string",
                    "enum": ["flow_agent", "logic_minimization_agent", "mapper_agent"],
                },
                "candidate_id": {"type": "string"},
                "benchmark_scope": {"type": "array", "items": {"type": "string"}},
                "risk_controls": {"type": "array", "items": {"type": "string"}},
                "rulebase_notes": {"type": "array", "items": {"type": "string"}},
            },
        }

    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        data = reply.parsed_json
        objective = str(data.get("cycle_objective", "TODO_CYCLE_OBJECTIVE"))
        selected_agent = str(data.get("selected_agent", "TODO_SELECTED_AGENT"))
        risk_controls = tuple(data.get("risk_controls", ("TODO_RISK_CONTROL",)))
        rulebase_notes = tuple(data.get("rulebase_notes", ("TODO_RULEBASE_NOTE",)))

        return AgentArtifacts(
            plan_markdown=(
                f"# Planning Agent Plan -- {self.context.candidate_id}\n\n"
                f"## Objective\n\n{objective}\n\n"
                f"## Selected Coding Agent\n\n- {selected_agent}\n\n"
                "## Risk Controls\n\n"
                f"{markdown_bullets(list(risk_controls))}"
            ),
            candidate_markdown=(
                "# Planner Candidate Dispatch\n\n"
                f"- Selected agent: {selected_agent}\n"
                "- TODO_AGENT_DISPATCH: create or update the coding-agent assignment.\n"
                f"- Evidence files read: {', '.join(evidence.keys())}\n"
            ),
            feedback_markdown=(
                "# Planning Feedback\n\n"
                "- TODO_PLANNER_FEEDBACK: fill after coding-agent validation.\n"
            ),
            rule_update_markdown=(
                "# Rulebase Update Proposal\n\n"
                f"{markdown_bullets(list(rulebase_notes))}"
            ),
            decision="TODO_PLANNING_DECISION",
        )

