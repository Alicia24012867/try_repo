"""Minimal cycle driver scaffold for paper-style agents."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from scripts.agents.self_evolved_abc.coding_agents.flow_agent import FlowAgent
from scripts.agents.self_evolved_abc.coding_agents.logic_minimization_agent import (
    LogicMinimizationAgent,
)
from scripts.agents.self_evolved_abc.coding_agents.mapper_agent import MapperAgent
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.model_client import build_model_client_from_env
from scripts.agents.self_evolved_abc.planning_agent import PlanningAgent


AGENT_TYPES = {
    "planning_agent": PlanningAgent,
    "flow_agent": FlowAgent,
    "logic_minimization_agent": LogicMinimizationAgent,
    "mapper_agent": MapperAgent,
}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one paper-style agent scaffold.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument(
        "--agent",
        choices=sorted(AGENT_TYPES),
        default="flow_agent",
        help="Agent role to instantiate for this candidate.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    context = CycleContext.from_assignment_file(args.repo_root, args.assignment)
    model_client = build_model_client_from_env()

    agent_cls = AGENT_TYPES[args.agent]
    agent = agent_cls(context=context, model_client=model_client)
    artifacts = agent.run()

    print(f"agent: {args.agent}")
    print(f"decision: {artifacts.decision}")
    print(f"cycle: {context.cycle_id}")
    print(f"candidate: {context.candidate_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

