"""Run one registered paper agent for an assignment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.model_client import build_model_client_from_env
from scripts.agents.self_evolved_abc.roles.registry import (
    agent_names,
    get_agent_spec,
    resolve_agent_class,
)


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

    model_client = build_model_client_from_env()
    agent_cls = resolve_agent_class(spec.name)
    agent = agent_cls(context=context, model_client=model_client)
    artifacts = agent.run()

    print(f"agent: {spec.name}")
    print(f"decision: {artifacts.decision}")
    print(f"cycle: {context.cycle_id}")
    print(f"candidate: {context.candidate_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
