"""Shared classification for coding outcomes that are not experiments."""

from __future__ import annotations


CODING_INFRASTRUCTURE_FAILURE_BUILD_STATUSES = frozenset(
    {
        "agent_provider_transient_failed",
        "agent_provider_permanent_failed",
        "agent_provider_configuration_failed",
        "agent_model_response_failed",
        "agent_preparation_failed",
    }
)


def is_coding_infrastructure_failure_status(value: object) -> bool:
    """Return whether a review describes infrastructure, not an experiment."""

    return str(value) in CODING_INFRASTRUCTURE_FAILURE_BUILD_STATUSES
