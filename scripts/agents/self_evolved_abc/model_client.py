"""LLM API boundary for paper-style agents.

Agents prepare prompts and expected response schemas. This module is the only
place that knows how to call a model provider.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ModelInvocation:
    """One model call prepared by an agent."""

    system_prompt: str
    user_prompt: str
    response_schema: Mapping[str, Any]
    model: str | None = "TODO_MODEL_NAME"
    temperature: float | None = None


@dataclass(frozen=True)
class ModelReply:
    """Raw and parsed model response."""

    raw_text: str
    parsed_json: Mapping[str, Any]


class ModelClient(Protocol):
    """Protocol every concrete model client must implement."""

    def complete_json(self, invocation: ModelInvocation) -> ModelReply:
        """Return a JSON object matching ``invocation.response_schema``."""


class ModelClientError(RuntimeError):
    """Base error for model-client failures."""


class ModelConfigurationError(ModelClientError):
    """Raised when environment configuration is missing or invalid."""


class ModelResponseError(ModelClientError):
    """Raised when model output is not a usable JSON object."""

    def __init__(
        self,
        message: str,
        *,
        failure_kind: str = "model_response",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.retryable = retryable


class ModelProviderTransientError(ModelClientError):
    """Raised for provider/network failures that may succeed on retry."""


class ModelProviderRequestError(ModelClientError):
    """Raised for non-retryable provider request/authentication failures."""


class TodoModelClient:
    """Placeholder client used until a provider is configured."""

    def complete_json(self, invocation: ModelInvocation) -> ModelReply:
        del invocation
        raise ModelConfigurationError(
            "No model provider configured. Set EDA_AGENT_MODEL_PROVIDER to "
            "'fixture', 'openai', or any OpenAI-compatible provider label."
        )


class FixtureModelClient:
    """Deterministic local client for testing the scaffold without network."""

    def __init__(self, fixture_json: Mapping[str, Any]) -> None:
        self.fixture_json = dict(fixture_json)

    @classmethod
    def from_env(cls) -> "FixtureModelClient":
        inline_json = os.environ.get("EDA_AGENT_MODEL_FIXTURE_JSON", "").strip()
        fixture_path = os.environ.get("EDA_AGENT_MODEL_FIXTURE_PATH", "").strip()

        if inline_json:
            payload = _parse_json_object(
                inline_json, source="EDA_AGENT_MODEL_FIXTURE_JSON"
            )
            return cls(payload)

        if fixture_path:
            path = Path(fixture_path)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ModelConfigurationError(
                    f"Cannot read fixture JSON file: {path}"
                ) from exc
            payload = _parse_json_object(
                text,
                source=str(path),
                retryable=False,
            )
            return cls(payload)

        raise ModelConfigurationError(
            "Fixture provider requires EDA_AGENT_MODEL_FIXTURE_JSON or "
            "EDA_AGENT_MODEL_FIXTURE_PATH."
        )

    def complete_json(self, invocation: ModelInvocation) -> ModelReply:
        del invocation
        raw_text = json.dumps(self.fixture_json, indent=2, sort_keys=True)
        return ModelReply(raw_text=raw_text, parsed_json=self.fixture_json)


class OpenAIModelClient:
    """OpenAI SDK client for OpenAI and OpenAI-compatible providers."""

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 16384,
        max_retries: int = 2,
        temperature: float = 0.2,
        response_format_mode: str = "json_object",
        strict_schema: bool = False,
        token_parameter: str = "max_tokens",
        trust_env: bool = True,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ModelConfigurationError(
                "OpenAI-compatible providers require the 'openai' Python package. "
                "Install it with: python3 -m pip install openai"
            ) from exc

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "max_retries": max_retries,
        }
        if trust_env:
            kwargs["timeout"] = timeout_seconds
        else:
            try:
                import httpx
            except ImportError as exc:
                raise ModelConfigurationError(
                    "Disabling proxy/env trust requires the 'httpx' package, "
                    "which should be installed with openai."
                ) from exc
            kwargs["http_client"] = httpx.Client(
                timeout=timeout_seconds,
                trust_env=False,
            )
        if base_url:
            kwargs["base_url"] = base_url
        if organization:
            kwargs["organization"] = organization
        if project:
            kwargs["project"] = project

        self.client = OpenAI(**kwargs)
        self.default_model = default_model
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.response_format_mode = response_format_mode
        self.strict_schema = strict_schema
        if token_parameter not in ("max_tokens", "max_completion_tokens"):
            raise ModelConfigurationError(
                "EDA_AGENT_MODEL_TOKEN_PARAMETER must be 'max_tokens' or "
                "'max_completion_tokens'."
            )
        self.token_parameter = token_parameter

    def complete_json(self, invocation: ModelInvocation) -> ModelReply:
        model = invocation.model
        if not model or model in ("TODO_MODEL", "TODO_MODEL_NAME"):
            model = self.default_model

        schema_instruction = ""
        if invocation.response_schema:
            schema_instruction = (
                "\n\nThe following JSON Schema is authoritative. Match its "
                "required fields and enums exactly:\n"
                + json.dumps(
                    dict(invocation.response_schema),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )

        request_args: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        invocation.system_prompt
                        + "\n\nReturn exactly one valid JSON object. "
                        + "Do not wrap it in Markdown."
                        + schema_instruction
                    ),
                },
                {"role": "user", "content": invocation.user_prompt},
            ],
            self.token_parameter: self.max_output_tokens,
        }

        temperature = (
            self.temperature
            if invocation.temperature is None
            else invocation.temperature
        )
        if temperature is not None:
            request_args["temperature"] = temperature

        response_format = self._response_format(invocation.response_schema)
        if response_format is not None:
            request_args["response_format"] = response_format

        try:
            completion = self.client.chat.completions.create(**request_args)
        except Exception as exc:
            status_code = _provider_status_code(exc)
            transient = _is_transient_provider_error(exc, status_code)
            error_type = (
                ModelProviderTransientError
                if transient
                else ModelProviderRequestError
            )
            raise error_type(
                "OpenAI-compatible API call failed "
                f"({type(exc).__name__}, status={status_code}): "
                f"{_bounded_exception_text(exc)}"
            ) from exc

        raw_text = self._extract_text(completion)
        parsed_json = _parse_json_object(
            raw_text,
            source="model response",
            retryable=True,
        )
        return ModelReply(raw_text=raw_text, parsed_json=parsed_json)

    def _response_format(
        self, schema: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        mode = self.response_format_mode.strip().lower()

        if mode in ("", "none", "disabled"):
            return None

        if mode == "json_object":
            return {"type": "json_object"}

        if mode == "json_schema":
            if not schema:
                return {"type": "json_object"}

            if self.strict_schema:
                issues = _strict_schema_issues(schema)
                if issues:
                    preview = "; ".join(issues[:5])
                    raise ModelConfigurationError(
                        "EDA_AGENT_MODEL_STRICT_SCHEMA=true requires every JSON "
                        "object to set additionalProperties=false and require "
                        "all declared properties. The current agent schema is "
                        f"not strict-compatible: {preview}. Use json_object or "
                        "disable strict mode until the schema contract is made "
                        "nullable/fully required."
                    )

            json_schema: dict[str, Any] = {
                "name": "agent_reply",
                "schema": dict(schema),
            }
            if self.strict_schema:
                json_schema["strict"] = True

            return {"type": "json_schema", "json_schema": json_schema}

        raise ModelConfigurationError(
            "EDA_AGENT_MODEL_RESPONSE_FORMAT must be one of: "
            "json_object, json_schema, none."
        )

    def _extract_text(self, completion: Any) -> str:
        choices = getattr(completion, "choices", None)
        if not choices:
            raise ModelProviderTransientError(
                "Provider response did not include choices."
            )

        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise ModelResponseError(
                "Model output hit the configured token/context limit.",
                failure_kind="length",
                retryable=True,
            )
        if finish_reason == "content_filter":
            raise ModelResponseError(
                "Model output was blocked by content filtering.",
                failure_kind="content_filter",
                retryable=False,
            )
        if finish_reason == "insufficient_system_resource":
            raise ModelProviderTransientError(
                "Provider stopped with insufficient_system_resource."
            )
        if finish_reason == "tool_calls":
            raise ModelResponseError(
                "Provider returned tool_calls instead of the required JSON object.",
                failure_kind="unexpected_tool_calls",
                retryable=False,
            )

        message = getattr(choice, "message", None)
        if message is None:
            raise ModelProviderTransientError(
                "Provider response missing choices[0].message."
            )

        refusal = getattr(message, "refusal", None)
        if refusal:
            raise ModelResponseError(
                f"Model refused the request: {refusal}",
                failure_kind="refusal",
                retryable=False,
            )

        return _content_to_text(getattr(message, "content", None))


def build_model_client_from_env() -> ModelClient:
    """Create a model client from environment variables.

    Any provider with an OpenAI-compatible chat completions API can be used by
    setting EDA_AGENT_MODEL_PROVIDER plus model/base-url/API-key variables.
    """

    provider = os.environ.get("EDA_AGENT_MODEL_PROVIDER", "todo").strip().lower()

    if provider in ("", "todo", "none"):
        return TodoModelClient()

    if provider == "fixture":
        return FixtureModelClient.from_env()

    provider_prefix = _provider_env_prefix(provider)

    base_url = _first_optional_env(
        "EDA_AGENT_MODEL_BASE_URL",
        f"{provider_prefix}_BASE_URL",
        "OPENAI_BASE_URL" if provider in ("openai", "openai-compatible") else "",
    )
    if provider != "openai" and not base_url:
        raise ModelConfigurationError(
            "OpenAI-compatible providers require EDA_AGENT_MODEL_BASE_URL "
            f"or {provider_prefix}_BASE_URL."
        )

    api_key = _first_required_env(
        "API key",
        "EDA_AGENT_MODEL_API_KEY",
        f"{provider_prefix}_API_KEY",
        "OPENAI_API_KEY" if provider in ("openai", "openai-compatible") else "",
    )
    model = _first_required_env(
        "model name",
        "EDA_AGENT_MODEL_NAME",
        f"{provider_prefix}_MODEL_NAME",
        "OPENAI_MODEL" if provider in ("openai", "openai-compatible") else "",
    )

    return OpenAIModelClient(
        api_key=api_key,
        default_model=model,
        base_url=base_url,
        organization=_optional_env("OPENAI_ORG_ID") if provider == "openai" else None,
        project=_optional_env("OPENAI_PROJECT_ID") if provider == "openai" else None,
        timeout_seconds=_env_float("EDA_AGENT_MODEL_TIMEOUT_SECONDS", 120.0),
        max_output_tokens=_env_int("EDA_AGENT_MODEL_MAX_OUTPUT_TOKENS", 16384),
        max_retries=_env_int("EDA_AGENT_MODEL_RETRIES", 2),
        temperature=_env_float("EDA_AGENT_MODEL_TEMPERATURE", 0.2),
        response_format_mode=os.environ.get(
            "EDA_AGENT_MODEL_RESPONSE_FORMAT", "json_object"
        ).strip(),
        strict_schema=_env_bool("EDA_AGENT_MODEL_STRICT_SCHEMA", False),
        token_parameter=os.environ.get(
            "EDA_AGENT_MODEL_TOKEN_PARAMETER", "max_tokens"
        ).strip(),
        trust_env=_env_bool("EDA_AGENT_MODEL_TRUST_ENV", True),
    )


def _provider_env_prefix(provider: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in provider.upper())
    return "_".join(part for part in normalized.split("_") if part)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ModelConfigurationError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _first_optional_env(*names: str) -> str | None:
    for name in names:
        if not name:
            continue
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _first_required_env(label: str, *names: str) -> str:
    value = _first_optional_env(*names)
    if value:
        return value

    shown = ", ".join(name for name in names if name)
    raise ModelConfigurationError(f"Missing {label}. Set one of: {shown}")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ModelConfigurationError(f"{name} must be an integer.") from exc


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ModelConfigurationError(f"{name} must be a number.") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ModelConfigurationError(f"{name} must be a boolean.")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif hasattr(item, "text") and isinstance(item.text, str):
                parts.append(item.text)
        text = "\n".join(parts).strip()
        if text:
            return text

    raise ModelResponseError(
        "Model message content was empty or not text.",
        failure_kind="empty_content",
        retryable=True,
    )


def _provider_status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    if not isinstance(value, int):
        response = getattr(error, "response", None)
        value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _is_transient_provider_error(
    error: BaseException,
    status_code: int | None,
) -> bool:
    class_name = type(error).__name__.lower()
    return (
        status_code in (408, 409, 429)
        or (isinstance(status_code, int) and status_code >= 500)
        or any(
            marker in class_name
            for marker in ("timeout", "connection", "ratelimit")
        )
    )


def _bounded_exception_text(error: BaseException, max_chars: int = 2000) -> str:
    text = " ".join(str(error).split())
    for name, value in os.environ.items():
        if "API_KEY" in name.upper() and len(value) >= 8:
            text = text.replace(value, "[REDACTED_API_KEY]")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 16].rstrip() + " ...[truncated]"


def _strict_schema_issues(
    schema: Mapping[str, Any],
    *,
    path: str = "$",
) -> tuple[str, ...]:
    """Return provider-strict incompatibilities without changing local semantics."""

    issues: list[str] = []
    schema_type = schema.get("type")
    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            issues.append(f"{path}.properties is not an object")
            return tuple(issues)
        if schema.get("additionalProperties") is not False:
            issues.append(f"{path}.additionalProperties must be false")
        required = schema.get("required", ())
        required_names = (
            set(required)
            if isinstance(required, list)
            and all(isinstance(item, str) for item in required)
            else set()
        )
        property_names = set(properties)
        if required_names != property_names:
            missing = sorted(property_names - required_names)
            extra = sorted(required_names - property_names)
            detail = []
            if missing:
                detail.append("not required=" + ",".join(missing))
            if extra:
                detail.append("unknown required=" + ",".join(extra))
            issues.append(f"{path}.required mismatch ({'; '.join(detail)})")
        for name, child in properties.items():
            if isinstance(child, Mapping):
                issues.extend(
                    _strict_schema_issues(child, path=f"{path}.{name}")
                )
    elif schema_type == "array":
        items = schema.get("items")
        if isinstance(items, Mapping):
            issues.extend(_strict_schema_issues(items, path=f"{path}[]"))
    else:
        for keyword in ("anyOf", "oneOf", "allOf"):
            branches = schema.get(keyword)
            if isinstance(branches, list):
                for index, branch in enumerate(branches):
                    if isinstance(branch, Mapping):
                        issues.extend(
                            _strict_schema_issues(
                                branch,
                                path=f"{path}.{keyword}[{index}]",
                            )
                        )
    return tuple(issues)


def _parse_json_object(
    text: str,
    *,
    source: str,
    retryable: bool = False,
) -> Mapping[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:500].replace("\n", "\\n")
        raise ModelResponseError(
            f"{source} was not valid JSON. Preview: {preview}",
            failure_kind="invalid_json",
            retryable=retryable,
        ) from exc

    if not isinstance(parsed, dict):
        raise ModelResponseError(
            f"{source} must be one JSON object.",
            failure_kind="non_object_json",
            retryable=retryable,
        )

    return parsed
