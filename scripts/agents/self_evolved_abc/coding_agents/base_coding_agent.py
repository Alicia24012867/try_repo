"""Base class for paper-style coding agents."""

from __future__ import annotations

from typing import Any, Mapping

from scripts.agents.self_evolved_abc.base_agent import PaperAgent
from scripts.agents.self_evolved_abc.model_client import ModelInvocation, ModelReply
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts, markdown_bullets


class CodingAgent(PaperAgent):
    """Common scaffold for Flow, Logic Minimization, and Mapper agents."""

    allowed_subsystems: tuple[str, ...] = ()
    candidate_kind = "TODO_CANDIDATE_KIND"

    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        system_prompt = (
            f"You are the {self.paper_role} in a small reproduction of "
            "Multi-Agent Self-Evolved ABC. Produce one conservative candidate. "
            "Do not modify source code directly in this first scaffold."
        )
        user_prompt = (
            f"Assignment: {self.context.assignment}\n\n"
            f"Allowed subsystems: {self.allowed_subsystems}\n\n"
            f"Evidence files: {', '.join(evidence.keys())}\n\n"
            "TODO_CODING_PROMPT_RENDER: render coding_agent_prompt.md with "
            "summary.csv, skipped.csv, run_notes.md, rulebase, and role-specific "
            "constraints. Require strict JSON output."
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
                "rationale",
                "candidate_kind",
                "candidate_steps",
                "expected_effect",
                "risks",
                "validation_plan",
                "rule_updates",
            ],
            "properties": {
                "rationale": {"type": "string"},
                "candidate_kind": {"type": "string"},
                "candidate_steps": {"type": "array", "items": {"type": "string"}},
                "expected_effect": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "string"}},
                "validation_plan": {"type": "array", "items": {"type": "string"}},
                "rule_updates": {"type": "array", "items": {"type": "string"}},
            },
        }

    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        data = reply.parsed_json
        rationale = str(data.get("rationale", "TODO_RATIONALE"))
        candidate_steps = tuple(data.get("candidate_steps", ("TODO_CANDIDATE_STEP",)))
        risks = tuple(data.get("risks", ("TODO_RISK",)))
        validation_plan = tuple(data.get("validation_plan", ("TODO_VALIDATION_PLAN",)))
        rule_updates = tuple(data.get("rule_updates", ("TODO_RULE_UPDATE",)))

        return AgentArtifacts(
            plan_markdown=(
                f"# {self.paper_role} Plan -- {self.context.candidate_id}\n\n"
                f"## Rationale\n\n{rationale}\n\n"
                "## Candidate Steps\n\n"
                f"{markdown_bullets(list(candidate_steps))}"
            ),
            candidate_markdown=(
                f"# {self.paper_role} Candidate -- {self.context.candidate_id}\n\n"
                f"- Candidate kind: {data.get('candidate_kind', self.candidate_kind)}\n"
                "- TODO_CANDIDATE_MATERIALIZATION: write flow/script/patch only "
                "after strict schema validation and scope checks.\n\n"
                "## Evidence Files\n\n"
                f"{markdown_bullets(list(evidence.keys()))}"
            ),
            feedback_markdown=(
                f"# {self.paper_role} Feedback -- {self.context.candidate_id}\n\n"
                "## Validation Plan\n\n"
                f"{markdown_bullets(list(validation_plan))}\n\n"
                "## Risks\n\n"
                f"{markdown_bullets(list(risks))}"
            ),
            rule_update_markdown=(
                f"# {self.paper_role} Rule Updates -- {self.context.candidate_id}\n\n"
                f"{markdown_bullets(list(rule_updates))}"
            ),
            decision="TODO_CODING_AGENT_DECISION",
        )

