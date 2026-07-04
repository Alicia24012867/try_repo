# Agent Template Notes

The executable reference agent is implemented in Python:

- `scripts/agents/simple_agent.py`

The Markdown file in this directory is only the companion prompt/specification
for humans and LLM calls. It is not the runnable agent body.

## Files

- `simple_agent_template.md`: prompt/spec skeleton for describing a new agent's
  role, scope, validation gates, and report format.

## Recommended Use

1. Copy `scripts/agents/simple_agent.py` when you need a new executable agent.
2. Use `simple_agent_template.md` to fill in the agent's role and prompt text.
3. Keep the Python path checks, validation flow, and report writing behavior
   unless there is a concrete reason to change them.
