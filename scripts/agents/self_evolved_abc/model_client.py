"""LLM API boundary for paper-style agents.

This file deliberately does not bind the project to a concrete vendor yet. The
first real implementation should plug in an API client here, not inside the
individual agents.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ModelInvocation:
    """One model call prepared by an agent."""

    system_prompt: str
    user_prompt: str
    response_schema: Mapping[str, Any]
    model: str = "TODO_MODEL_NAME"
    temperature: float = 0.2


@dataclass(frozen=True)
class ModelReply:
    """Raw and parsed model response."""

    raw_text: str
    parsed_json: Mapping[str, Any]


class ModelClient(Protocol):
    """Protocol every concrete model client must implement."""

    def complete_json(self, invocation: ModelInvocation) -> ModelReply:
        """Return a JSON object matching ``invocation.response_schema``."""


class TodoModelClient:
    """Placeholder client used until the API integration is selected."""

    def complete_json(self, invocation: ModelInvocation) -> ModelReply:
        del invocation
        raise NotImplementedError(
            "TODO_LLM_API_CLIENT: connect an OpenAI-compatible or local model "
            "client here, then return ModelReply(raw_text=..., parsed_json=...)."
        )


def build_model_client_from_env() -> ModelClient:
    """Create a model client from environment variables.

    TODO_LLM_API_CLIENT:
    - read the provider, model name, API key, and base URL from env vars;
    - construct the concrete client;
    - keep retries and rate-limit handling at this boundary.
    """

    provider = os.environ.get("EDA_AGENT_MODEL_PROVIDER", "TODO")
    if provider == "TODO":
        return TodoModelClient()

    raise NotImplementedError(
        f"TODO_LLM_API_CLIENT: unsupported provider placeholder {provider!r}."
    )

