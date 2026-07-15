"""Run one registered paper agent for an assignment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.model_client import (
    ModelClientError,
    ModelConfigurationError,
    ModelProviderRequestError,
    ModelProviderTransientError,
    ModelResponseError,
    build_model_client_from_env,
)
from scripts.agents.self_evolved_abc.roles.registry import (
    agent_names,
    get_agent_spec,
    resolve_agent_class,
)
from scripts.agents.self_evolved_abc.workflow.artifacts import agent_attempt_path


EXIT_MODEL_TRANSIENT = 10
EXIT_MODEL_RESPONSE_RETRYABLE = 11
EXIT_MODEL_PERMANENT = 12
EXIT_MODEL_CONFIGURATION = 13
EXIT_AGENT_PREPARATION = 14


# Read-only registry view retained for callers that need to display role
# metadata.  Concrete classes remain lazy so importing the driver has no model
# or role-specific side effects.
AGENT_TYPES = {name: get_agent_spec(name) for name in agent_names()}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one registered paper agent.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument(
        "--agent",
        choices=agent_names(),
        default=None,
        help=(
            "Agent role to instantiate. Defaults to the assignment's exact "
            "registered role."
        ),
    )
    parser.add_argument("--attempt-index", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    assignment_path = (
        args.assignment
        if args.assignment.is_absolute()
        else repo_root / args.assignment
    )
    context = CycleContext.from_assignment_file(repo_root, assignment_path)
    requested_agent = args.agent or context.agent_name
    spec = get_agent_spec(requested_agent)
    if args.agent is not None and context.agent_name != spec.name:
        print(
            "cycle_driver: --agent does not match assignment agent_name: "
            f"{requested_agent!r} != {context.agent_name!r}",
            file=sys.stderr,
        )
        return 2
    if context.paper_role != spec.paper_role:
        print(
            "cycle_driver: assignment paper_role does not match registered role: "
            f"{context.paper_role!r} != {spec.paper_role!r}",
            file=sys.stderr,
        )
        return 2

    if args.attempt_index < 1:
        print("cycle_driver: --attempt-index must be >= 1", file=sys.stderr)
        return 2
    try:
        model_client = build_model_client_from_env()
        agent_cls = resolve_agent_class(spec.name)
        agent = agent_cls(context=context, model_client=model_client)
        artifacts = agent.run()
    except ModelConfigurationError as exc:
        _write_attempt_status(
            context,
            args.attempt_index,
            status="failed",
            failure_kind="provider_configuration",
            retryable=False,
            error=exc,
        )
        print(f"cycle_driver: provider_configuration: {exc}", file=sys.stderr)
        return EXIT_MODEL_CONFIGURATION
    except ModelProviderTransientError as exc:
        _write_attempt_status(
            context,
            args.attempt_index,
            status="failed",
            failure_kind="provider_transient",
            retryable=True,
            error=exc,
        )
        print(f"cycle_driver: provider_transient: {exc}", file=sys.stderr)
        return EXIT_MODEL_TRANSIENT
    except ModelResponseError as exc:
        failure_kind = f"model_response_{exc.failure_kind}"
        _write_attempt_status(
            context,
            args.attempt_index,
            status="failed",
            failure_kind=failure_kind,
            retryable=exc.retryable,
            error=exc,
        )
        print(
            f"cycle_driver: {failure_kind} "
            f"retryable={str(exc.retryable).lower()}: {exc}",
            file=sys.stderr,
        )
        return (
            EXIT_MODEL_RESPONSE_RETRYABLE
            if exc.retryable
            else EXIT_MODEL_PERMANENT
        )
    except (ModelProviderRequestError, ModelClientError) as exc:
        _write_attempt_status(
            context,
            args.attempt_index,
            status="failed",
            failure_kind="provider_permanent",
            retryable=False,
            error=exc,
        )
        print(f"cycle_driver: provider_permanent: {exc}", file=sys.stderr)
        return EXIT_MODEL_PERMANENT
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _write_attempt_status(
            context,
            args.attempt_index,
            status="failed",
            failure_kind="agent_preparation",
            retryable=False,
            error=exc,
        )
        print(f"cycle_driver: agent_preparation: {exc}", file=sys.stderr)
        return EXIT_AGENT_PREPARATION

    _write_attempt_status(
        context,
        args.attempt_index,
        status="completed",
        failure_kind="",
        retryable=False,
        decision=artifacts.decision,
    )

    print(f"agent: {spec.name}")
    print(f"decision: {artifacts.decision}")
    print(f"cycle: {context.cycle_id}")
    print(f"candidate: {context.candidate_id}")
    return 0


def _write_attempt_status(
    context: CycleContext,
    attempt: int,
    *,
    status: str,
    failure_kind: str,
    retryable: bool,
    decision: str = "",
    error: BaseException | None = None,
) -> Path:
    payload: dict[str, object] = {
        "schema_version": 1,
        "cycle_id": context.cycle_id,
        "candidate_id": context.candidate_id,
        "agent_name": context.agent_name,
        "attempt": attempt,
        "status": status,
        "failure_kind": failure_kind,
        "retryable": retryable,
        "decision": decision,
        "error_type": type(error).__name__ if error is not None else "",
        "error_message": _bounded_error(error),
    }
    path = agent_attempt_path(context, attempt, "status")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    print(f"cycle_driver: attempt_status={path}")
    return path


def _bounded_error(error: BaseException | None) -> str:
    if error is None:
        return ""
    text = " ".join(str(error).split())
    return text[:4000]


if __name__ == "__main__":
    raise SystemExit(main())
