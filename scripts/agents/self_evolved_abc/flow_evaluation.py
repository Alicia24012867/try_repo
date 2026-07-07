"""Compatibility entrypoint for Flow Agent evaluation planning."""

from scripts.agents.self_evolved_abc.flow.evaluation import *  # noqa: F401,F403
from scripts.agents.self_evolved_abc.flow.evaluation import main


if __name__ == "__main__":
    raise SystemExit(main())

