"""Compatibility entrypoint for Flow Agent evaluation runs."""

from scripts.agents.self_evolved_abc.flow.runner import *  # noqa: F401,F403
from scripts.agents.self_evolved_abc.flow.runner import main


if __name__ == "__main__":
    raise SystemExit(main())

