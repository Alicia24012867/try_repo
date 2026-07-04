#!/usr/bin/env python3
"""Reference Python template for a small paper-style reproduction agent.

This module is intentionally modest: it gives future agents a safe execution
shape without pretending to implement autonomous code evolution. Subclasses can
override the TODO hooks, while the base class keeps assignment parsing,
path checks, optional validation commands, and report writing consistent.

Example:
    python -m scripts.agents.simple_agent \
        --agent-name flow_tuning_agent \
        --paper-role "Flow Agent" \
        --cycle-id cycle_001 \
        --candidate-id candidate_001 \
        --subsystem third_party/FlowTune/src \
        --planner-hypothesis "Improve diagnostic logging without changing QoR" \
        --target-metric diagnostic_value
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"
NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


@dataclass(frozen=True)
class ValidationCommand:
    """A command that may be run after the agent prepares a candidate."""

    name: str
    argv: tuple[str, ...]
    timeout_seconds: int = 600

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ValidationCommand":
        name = str(value.get("name", "unnamed"))
        raw_argv = value.get("argv", value.get("command", ()))
        if isinstance(raw_argv, str):
            raise ValueError(
                f"Validation command {name!r} must use an argv list, not a shell string."
            )
        argv = tuple(str(part) for part in raw_argv)
        if not argv:
            raise ValueError(f"Validation command {name!r} has an empty argv list.")
        timeout_seconds = int(value.get("timeout_seconds", 600))
        return cls(name=name, argv=argv, timeout_seconds=timeout_seconds)


@dataclass(frozen=True)
class Assignment:
    """Planner-provided inputs for one agent candidate."""

    agent_name: str
    paper_role: str
    cycle_id: str
    candidate_id: str
    subsystem: str
    planner_hypothesis: str
    target_metric: str
    secondary_metrics: tuple[str, ...] = ()
    benchmark_scope: tuple[str, ...] = ()
    allowed_to_read: tuple[str, ...] = ()
    allowed_to_edit: tuple[str, ...] = ()
    recent_evidence: tuple[str, ...] = ()
    validation_commands: tuple[ValidationCommand, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Assignment":
        return cls(
            agent_name=str(value["agent_name"]),
            paper_role=str(value["paper_role"]),
            cycle_id=str(value["cycle_id"]),
            candidate_id=str(value["candidate_id"]),
            subsystem=str(value["subsystem"]),
            planner_hypothesis=str(value["planner_hypothesis"]),
            target_metric=str(value["target_metric"]),
            secondary_metrics=_as_tuple(value.get("secondary_metrics", ())),
            benchmark_scope=_as_tuple(value.get("benchmark_scope", ())),
            allowed_to_read=_as_tuple(value.get("allowed_to_read", ())),
            allowed_to_edit=_as_tuple(value.get("allowed_to_edit", ())),
            recent_evidence=_as_tuple(value.get("recent_evidence", ())),
            validation_commands=tuple(
                ValidationCommand.from_mapping(item)
                for item in value.get("validation_commands", ())
            ),
        )


@dataclass(frozen=True)
class CommandResult:
    """Captured result from an optional validation command."""

    name: str
    argv: tuple[str, ...]
    status: str
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str = ""


@dataclass(frozen=True)
class AgentResult:
    """Structured result written into the experiment agent folders."""

    decision: str
    summary: str
    orientation: tuple[str, ...]
    candidate_plan: tuple[str, ...]
    implementation_notes: tuple[str, ...]
    validation_results: tuple[CommandResult, ...]
    risks: tuple[str, ...]
    rule_updates: tuple[str, ...]


@dataclass(frozen=True)
class AgentPaths:
    """Canonical output locations for one experiment cycle."""

    plan_file: Path
    candidate_file: Path
    feedback_file: Path
    rule_update_file: Path

    @classmethod
    def for_assignment(cls, repo_root: Path, assignment: Assignment) -> "AgentPaths":
        base = repo_root / "experiments" / assignment.cycle_id / "agents"
        candidate = f"{assignment.candidate_id}.md"
        return cls(
            plan_file=base / "plans" / candidate,
            candidate_file=base / "candidate_changes" / candidate,
            feedback_file=base / "feedback" / candidate,
            rule_update_file=base / "rule_updates" / candidate,
        )

    def ensure_parent_dirs(self) -> None:
        for path in (
            self.plan_file,
            self.candidate_file,
            self.feedback_file,
            self.rule_update_file,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)


class SimpleAgent:
    """Safe base class for future reproduction agents.

    The default implementation does not edit source code. It gives a future
    agent author four clean extension points:

    - ``orient``
    - ``design_candidate``
    - ``implement_candidate``
    - ``summarize_risks``

    Keep this class boring. Put experiment-specific intelligence in subclasses.
    """

    def __init__(
        self,
        repo_root: Path,
        assignment: Assignment,
        *,
        run_validation: bool = False,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.assignment = assignment
        self.run_validation = run_validation
        self.paths = AgentPaths.for_assignment(self.repo_root, assignment)

    def run(self) -> AgentResult:
        self._check_assignment_paths()
        orientation = self.orient()
        candidate_plan = self.design_candidate(orientation)
        implementation_notes = self.implement_candidate(candidate_plan)
        validation_results = self.validate_candidate()
        risks = self.summarize_risks(validation_results)
        rule_updates = self.propose_rule_updates(validation_results)
        decision = self.decide(validation_results)

        result = AgentResult(
            decision=decision,
            summary=self.summarize_decision(decision, validation_results),
            orientation=orientation,
            candidate_plan=candidate_plan,
            implementation_notes=implementation_notes,
            validation_results=validation_results,
            risks=risks,
            rule_updates=rule_updates,
        )
        self.write_reports(result)
        return result

    def orient(self) -> tuple[str, ...]:
        """Read assignment context and describe what this agent knows.

        TODO(agent): Replace or extend this method in a real agent. A stronger
        implementation should inspect source entry points, logs, and prior
        candidate notes before designing a change.
        """

        lines = [
            f"Agent role: {self.assignment.paper_role}.",
            f"Subsystem boundary: {self.assignment.subsystem}.",
            f"Planner hypothesis: {self.assignment.planner_hypothesis}.",
            f"Target metric: {self.assignment.target_metric}.",
        ]
        lines.extend(self._describe_paths("Allowed read paths", self.assignment.allowed_to_read))
        lines.extend(self._describe_paths("Recent evidence", self.assignment.recent_evidence))
        return tuple(lines)

    def design_candidate(self, orientation: Sequence[str]) -> tuple[str, ...]:
        """Design the smallest candidate that could test the hypothesis.

        TODO(agent): Replace this placeholder with actual candidate selection.
        Keep the plan narrow enough that validation can attribute the result to
        one mechanism.
        """

        del orientation
        return (
            "TODO(agent): Identify the smallest code location that can test the hypothesis.",
            "TODO(agent): State the expected before/after behavior.",
            "TODO(agent): State the compile, CEC, and QoR evidence needed for acceptance.",
        )

    def implement_candidate(self, candidate_plan: Sequence[str]) -> tuple[str, ...]:
        """Implement the selected candidate.

        The base template is intentionally a no-op. This prevents accidental
        source changes when the template is run directly.
        """

        del candidate_plan
        return (
            "SKIPPED: Base template does not modify source code.",
            "TODO(agent): Implement this method in a subclass or copied agent.",
        )

    def validate_candidate(self) -> tuple[CommandResult, ...]:
        """Run optional validation commands using argv lists, never shell text."""

        if not self.assignment.validation_commands:
            return (
                CommandResult(
                    name="validation",
                    argv=(),
                    status=SKIPPED,
                    returncode=None,
                    error="No validation_commands were provided.",
                ),
            )
        if not self.run_validation:
            return tuple(
                CommandResult(
                    name=command.name,
                    argv=command.argv,
                    status=SKIPPED,
                    returncode=None,
                    error="Validation command recorded but not run. Pass --run-validation to execute.",
                )
                for command in self.assignment.validation_commands
            )

        return tuple(self._run_command(command) for command in self.assignment.validation_commands)

    def summarize_risks(
        self, validation_results: Sequence[CommandResult]
    ) -> tuple[str, ...]:
        failures = [item for item in validation_results if item.status == FAIL]
        if failures:
            return (
                "Correctness or reproducibility risk remains because at least one validation failed.",
                "Do not compare QoR until compile and CEC gates pass.",
            )
        return (
            "TODO(agent): Fill in correctness, runtime, and generalization risks.",
            "Default template made no source changes, so measured QoR should not be claimed.",
        )

    def propose_rule_updates(
        self, validation_results: Sequence[CommandResult]
    ) -> tuple[str, ...]:
        if any(item.status == FAIL for item in validation_results):
            return ("TODO(agent): Convert the failure into a reusable rule before the next cycle.",)
        return ("No rule update proposed by the base template.",)

    def decide(self, validation_results: Sequence[CommandResult]) -> str:
        if any(item.status == FAIL for item in validation_results):
            return "REJECT"
        if any(item.status in {SKIPPED, NEEDS_HUMAN_REVIEW} for item in validation_results):
            return "REQUEST_PLANNER_REVIEW"
        return "ACCEPT"

    def summarize_decision(
        self, decision: str, validation_results: Sequence[CommandResult]
    ) -> str:
        failed = [item.name for item in validation_results if item.status == FAIL]
        skipped = [item.name for item in validation_results if item.status == SKIPPED]
        if failed:
            return f"{decision}: failed validation gates: {', '.join(failed)}."
        if skipped:
            return f"{decision}: validation was not complete: {', '.join(skipped)}."
        return f"{decision}: all provided validation gates passed."

    def write_reports(self, result: AgentResult) -> None:
        self.paths.ensure_parent_dirs()
        self.paths.plan_file.write_text(self._render_plan(result), encoding="utf-8")
        self.paths.candidate_file.write_text(
            self._render_candidate(result), encoding="utf-8"
        )
        self.paths.feedback_file.write_text(
            self._render_feedback(result), encoding="utf-8"
        )
        self.paths.rule_update_file.write_text(
            self._render_rule_update(result), encoding="utf-8"
        )

    def _check_assignment_paths(self) -> None:
        for label, values in (
            ("allowed_to_read", self.assignment.allowed_to_read),
            ("allowed_to_edit", self.assignment.allowed_to_edit),
            ("recent_evidence", self.assignment.recent_evidence),
            ("benchmark_scope", self.assignment.benchmark_scope),
        ):
            for value in values:
                self._resolve_inside_repo(value, label)

    def _resolve_inside_repo(self, value: str, label: str) -> Path:
        path = (self.repo_root / value).resolve()
        try:
            path.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError(f"{label} path escapes repository: {value}") from exc
        return path

    def _describe_paths(self, title: str, values: Iterable[str]) -> tuple[str, ...]:
        values = tuple(values)
        if not values:
            return (f"{title}: none provided.",)

        lines = [f"{title}:"]
        for value in values:
            path = self._resolve_inside_repo(value, title)
            state = "exists" if path.exists() else "missing"
            lines.append(f"- {value} ({state})")
        return tuple(lines)

    def _run_command(self, command: ValidationCommand) -> CommandResult:
        try:
            completed = subprocess.run(
                command.argv,
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=command.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                name=command.name,
                argv=command.argv,
                status=FAIL,
                returncode=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                error=f"Timed out after {command.timeout_seconds} seconds.",
            )
        except OSError as exc:
            return CommandResult(
                name=command.name,
                argv=command.argv,
                status=FAIL,
                returncode=None,
                error=str(exc),
            )

        status = PASS if completed.returncode == 0 else FAIL
        return CommandResult(
            name=command.name,
            argv=command.argv,
            status=status,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _render_plan(self, result: AgentResult) -> str:
        return "\n".join(
            (
                f"# Plan -- {self.assignment.candidate_id}",
                "",
                *self._render_metadata(),
                "",
                "## Orientation",
                *_bullets(result.orientation),
                "",
                "## Candidate Plan",
                *_bullets(result.candidate_plan),
                "",
            )
        )

    def _render_candidate(self, result: AgentResult) -> str:
        return "\n".join(
            (
                f"# Candidate Change -- {self.assignment.candidate_id}",
                "",
                *self._render_metadata(),
                "",
                "## Implementation Notes",
                *_bullets(result.implementation_notes),
                "",
                "## Allowed Edit Scope",
                *_bullets(self.assignment.allowed_to_edit or ("No edit scope provided.",)),
                "",
            )
        )

    def _render_feedback(self, result: AgentResult) -> str:
        validation_lines = []
        for item in result.validation_results:
            validation_lines.extend(
                (
                    f"### {item.name}",
                    "",
                    f"- Status: {item.status}",
                    f"- Command: {_format_argv(item.argv)}",
                    f"- Return code: {item.returncode}",
                )
            )
            if item.error:
                validation_lines.append(f"- Error: {item.error}")
            if item.stdout:
                validation_lines.extend(("", "```text", _clip(item.stdout), "```"))
            if item.stderr:
                validation_lines.extend(("", "```text", _clip(item.stderr), "```"))
            validation_lines.append("")

        return "\n".join(
            (
                f"# Feedback -- {self.assignment.candidate_id}",
                "",
                *self._render_metadata(),
                "",
                "## Decision",
                "",
                f"- {result.summary}",
                "",
                "## Validation",
                *validation_lines,
                "## Risks",
                *_bullets(result.risks),
                "",
            )
        )

    def _render_rule_update(self, result: AgentResult) -> str:
        return "\n".join(
            (
                f"# Rule Update -- {self.assignment.candidate_id}",
                "",
                *self._render_metadata(),
                "",
                "## Proposed Updates",
                *_bullets(result.rule_updates),
                "",
            )
        )

    def _render_metadata(self) -> tuple[str, ...]:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            f"- Agent: {self.assignment.agent_name}",
            f"- Paper role: {self.assignment.paper_role}",
            f"- Cycle: {self.assignment.cycle_id}",
            f"- Candidate: {self.assignment.candidate_id}",
            f"- Generated at: {now}",
            f"- Subsystem: {self.assignment.subsystem}",
            f"- Target metric: {self.assignment.target_metric}",
        )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the reference Python agent template."
    )
    parser.add_argument("--assignment", type=Path, help="Optional assignment JSON.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-validation", action="store_true")
    parser.add_argument("--agent-name")
    parser.add_argument("--paper-role")
    parser.add_argument("--cycle-id")
    parser.add_argument("--candidate-id")
    parser.add_argument("--subsystem")
    parser.add_argument("--planner-hypothesis")
    parser.add_argument("--target-metric")
    parser.add_argument("--allowed-to-read", action="append")
    parser.add_argument("--allowed-to-edit", action="append")
    parser.add_argument("--recent-evidence", action="append")
    parser.add_argument("--benchmark", action="append")
    return parser.parse_args(argv)


def load_assignment(args: argparse.Namespace) -> Assignment:
    data: dict[str, Any] = {}
    if args.assignment:
        data.update(json.loads(args.assignment.read_text(encoding="utf-8")))

    cli_values = {
        "agent_name": args.agent_name,
        "paper_role": args.paper_role,
        "cycle_id": args.cycle_id,
        "candidate_id": args.candidate_id,
        "subsystem": args.subsystem,
        "planner_hypothesis": args.planner_hypothesis,
        "target_metric": args.target_metric,
        "allowed_to_read": _as_tuple(args.allowed_to_read),
        "allowed_to_edit": _as_tuple(args.allowed_to_edit),
        "recent_evidence": _as_tuple(args.recent_evidence),
        "benchmark_scope": _as_tuple(args.benchmark),
    }
    for key, value in cli_values.items():
        if value not in (None, ()):
            data[key] = value

    data.setdefault("cycle_id", "cycle_000")
    data.setdefault("candidate_id", "candidate_template")

    missing = [
        key
        for key in (
            "agent_name",
            "paper_role",
            "cycle_id",
            "candidate_id",
            "subsystem",
            "planner_hypothesis",
            "target_metric",
        )
        if not data.get(key)
    ]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required assignment fields: {joined}")

    return Assignment.from_mapping(data)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        assignment = load_assignment(args)
        agent = SimpleAgent(
            repo_root=args.repo_root,
            assignment=assignment,
            run_validation=args.run_validation,
        )
        result = agent.run()
    except Exception as exc:
        print(f"agent template failed: {exc}", file=sys.stderr)
        return 2

    print(result.summary)
    print(f"plan: {agent.paths.plan_file}")
    print(f"feedback: {agent.paths.feedback_file}")
    return 0


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _bullets(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(f"- {value}" for value in values)


def _clip(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value.rstrip()
    return value[:limit].rstrip() + "\n... [truncated]"


def _format_argv(argv: Sequence[str]) -> str:
    if not argv:
        return "(none)"
    return " ".join(_quote_arg(part) for part in argv)


def _quote_arg(value: str) -> str:
    if not value:
        return "''"
    if any(char.isspace() for char in value):
        return repr(value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
