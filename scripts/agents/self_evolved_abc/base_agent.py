"""Base classes for paper-style LLM agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.model_client import (
    ModelClient,
    ModelInvocation,
    ModelReply,
)
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts


class PaperAgent(ABC):
    """Common LLM-agent lifecycle.

    The flow mirrors the paper's agent loop at a small reproduction scale:
    collect evidence, build prompt, call model, parse proposal, write artifacts.
    """

    agent_name = "TODO_AGENT_NAME"
    paper_role = "TODO_PAPER_ROLE"
    prompt_template = "TODO_PROMPT_TEMPLATE"

    def __init__(self, context: CycleContext, model_client: ModelClient) -> None:
        self.context = context
        self.model_client = model_client

    def run(self) -> AgentArtifacts:
        evidence = self.load_evidence()
        invocation = self.build_model_invocation(evidence)
        reply = self.call_model(invocation)
        artifacts = self.materialize_reply(reply, evidence)
        self.write_artifacts(artifacts)
        return artifacts

    def load_evidence(self) -> Mapping[str, str]:
        """Load cycle evidence for the model prompt."""

        return self.context.read_evidence_text()

    def call_model(self, invocation: ModelInvocation) -> ModelReply:
        """Call the configured LLM API.

        TODO_LLM_API_CLIENT: The concrete API call lives in ``ModelClient``.
        Agents should not import vendor SDKs directly.
        """

        return self.model_client.complete_json(invocation)

    def write_artifacts(self, artifacts: AgentArtifacts) -> None:
        paths = self.context.artifact_paths()
        paths.ensure_parent_dirs()
        paths.plan.write_text(artifacts.plan_markdown, encoding="utf-8")
        paths.candidate_change.write_text(
            artifacts.candidate_markdown, encoding="utf-8"
        )
        paths.feedback.write_text(artifacts.feedback_markdown, encoding="utf-8")
        paths.rule_update.write_text(
            artifacts.rule_update_markdown, encoding="utf-8"
        )

    @abstractmethod
    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        """Build the model request for this agent role."""

    @abstractmethod
    def response_schema(self) -> Mapping[str, Any]:
        """Return the strict JSON schema expected from the model."""

    @abstractmethod
    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        """Convert model JSON into repo artifacts.

        TODO_PARSE_MODEL_JSON: Validate every required field before writing
        candidate files or downstream reports.
        """

