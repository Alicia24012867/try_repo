# Agent Templates

This directory contains copyable reference templates for adding new agents to
the paper-style multi-agent ABC evolution workflow.

## Available Templates

- `simple_agent_template.md`: a complete minimal agent specification and prompt
  skeleton. Use it as the starting point for any new specialized agent.

## How To Use

1. Copy the template into the target role directory, for example `coding/` or
   `planner/`.
2. Replace every `{{PLACEHOLDER}}`.
3. Fill each `TODO(agent)` item with concrete project details.
4. Keep the safety, validation, and reporting sections unless the planner
   explicitly changes the contract for that cycle.

