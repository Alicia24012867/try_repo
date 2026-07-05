"""Flow Agent scaffold."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.coding_agents.base_coding_agent import CodingAgent
from scripts.agents.self_evolved_abc.model_client import ModelInvocation, ModelReply
from scripts.agents.self_evolved_abc.prompt_rendering import (
    compact_text_block,
    find_unresolved_placeholders,
    load_template,
    render_template,
    summarize_csv,
    summarize_flow_scripts,
)
from scripts.agents.self_evolved_abc.response_validation import (
    validate_flow_agent_response,
)
from scripts.agents.self_evolved_abc.schemas import (
    AgentArtifacts,
    FlowAgentResponse,
    ValidationIssue,
)


class FlowAgent(CodingAgent):
    """Flow Agent for flow scheduling and FlowTune-related candidates."""

    agent_name = "flow_agent"
    paper_role = "Flow Agent"
    prompt_template = "configs/agents/prompts/coding_agent_prompt.md"
    allowed_subsystems = ("configs/flows", "third_party/FlowTune/src/opt/flowtune")
    candidate_kind = "abc_flow"

    def response_schema(self) -> Mapping[str, Any]:
        return {
            "type": "object",
            "required": [
                "rationale",
                "candidate_kind",
                "candidate_steps",
                "source_design",
                "expected_effect",
                "entry_points",
                "invariants",
                "risk_hotspots",
                "files_to_write",
                "compatibility_notes",
                "validation_plan",
                "risks",
                "rollback_plan",
                "rule_updates",
                "decision",
            ],
            "additionalProperties": False,
            "properties": {
                "rationale": {"type": "string"},
                "candidate_kind": {
                    "type": "string",
                    "enum": ["abc_flow", "diagnostic_only"],
                },
                "candidate_steps": {"type": "array", "items": {"type": "string"}},
                "source_design": {"type": "string"},
                "expected_effect": {"type": "string"},
                "entry_points": {"type": "array", "items": {"type": "string"}},
                "invariants": {"type": "array", "items": {"type": "string"}},
                "risk_hotspots": {"type": "array", "items": {"type": "string"}},
                "files_to_write": {"type": "array", "items": {"type": "string"}},
                "compatibility_notes": {"type": "object"},
                "validation_plan": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "rollback_plan": {"type": "string"},
                "rule_updates": {"type": "array", "items": {"type": "string"}},
                "decision": {
                    "type": "string",
                    "enum": [
                        "PROPOSE_CANDIDATE",
                        "NEEDS_PLANNER_APPROVAL",
                        "DEFER",
                        "NEEDS_HUMAN_REVIEW",
                    ],
                },
            },
        }

    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        template = load_template(self.context.repo_root, self.prompt_template)
        values = self._prompt_values(evidence)
        user_prompt = render_template(template, values)
        self._validate_rendered_prompt(user_prompt)

        system_prompt = (
            "You are the Flow Agent in a small reproduction of Multi-Agent "
            "Self-Evolved ABC. Propose one conservative ABC flow candidate. "
            "Return exactly one JSON object and do not include Markdown prose."
        )

        return ModelInvocation(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=self.response_schema(),
        )

    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        validation = validate_flow_agent_response(reply.parsed_json, self.context)
        if not validation.ok or validation.response is None:
            return self._failure_artifacts_from_validation(
                reply,
                validation.issues,
                evidence,
            )
        return self._success_artifacts_from_flow_response(
            validation.response,
            evidence,
        )

    def candidate_flow_path(self) -> Path:
        return (
            self.context.repo_root
            / "configs"
            / "flows"
            / f"{self.context.cycle_id}_{self.context.candidate_id}.abc"
        )

    def _prompt_values(self, evidence: Mapping[str, str]) -> dict[str, object]:
        assignment = self.context.assignment
        repo_root = self.context.repo_root
        previous_cycle = str(assignment.get("previous_cycle_id", "cycle_000"))

        summary_path = repo_root / "experiments" / previous_cycle / "results/summary.csv"
        skipped_path = repo_root / "experiments" / previous_cycle / "results/skipped.csv"
        run_notes_path = repo_root / "experiments" / previous_cycle / "results/run_notes.md"

        return {
            "REPO_ROOT": repo_root,
            "CYCLE_ID": self.context.cycle_id,
            "CANDIDATE_ID": self.context.candidate_id,
            "AGENT_NAME": self.context.agent_name,
            "PAPER_ROLE": self.context.paper_role,
            "SUBSYSTEM": "FlowTune / ABC flow scheduling",
            "DRY_RUN": str(assignment.get("dry_run", False)).lower(),
            "PLANNER_TASK": assignment.get("planner_hypothesis", ""),
            "ALLOWED_FILES": assignment.get("allowed_to_edit", ()),
            "PROGRAMMING_GUIDANCE": load_template(
                repo_root, "configs/agents/shared/programming_guidance.md"
            ),
            "RULEBASE": load_template(repo_root, "configs/agents/shared/rulebase.md"),
            "COMPILE_OR_RUNTIME_LOGS": self._runtime_context(evidence),
            "CEC_LOGS": (
                "CEC is not wired into cycle_000/cycle_001 yet. Treat all QoR "
                "as provisional process evidence until equivalence checks pass."
            ),
            "QOR_DELTAS": "\n\n".join(
                (
                    summarize_csv(summary_path, max_rows=20, max_chars=10000),
                    summarize_csv(skipped_path, max_rows=20, max_chars=4000),
                    self._read_optional_block("run_notes", run_notes_path, 6000),
                )
            ),
            "PREVIOUS_CANDIDATES": self._previous_flow_context(previous_cycle),
            "PRIMARY_METRIC": assignment.get(
                "target_metric", "AIG node count / depth provisional"
            ),
            "SECONDARY_METRICS": assignment.get(
                "secondary_metrics", ["depth", "runtime", "stability"]
            ),
            "REGRESSION_THRESHOLD": (
                "No hidden skipped designs; depth/runtime regressions must be "
                "reported per benchmark."
            ),
            "RUNTIME_BUDGET": "small EPFL subset; keep flow length conservative",
            "BENCHMARK_SCOPE": assignment.get("benchmark_scope", ()),
            "FLOW_SCOPE": (
                "ABC commands only. Do not include shell commands, benchmark-name "
                "branches, redirection, pipes, or previous-cycle output writes."
            ),
            "COMPILE_COMMAND": "SKIPPED: flow-only candidate changes no C source.",
            "SMOKE_COMMAND": self._smoke_command(),
            "CEC_COMMAND": (
                "TODO: run ABC cec between baseline and candidate AIGs after "
                "the evaluation runner is wired."
            ),
            "QOR_COMMAND": self._qor_command(),
            "COMPILE_PASS_CONDITION": (
                "Recorded as SKIPPED with reason: no source code changed."
            ),
            "SMOKE_PASS_CONDITION": "ABC exits 0 and prints parseable ps statistics.",
            "CEC_PASS_CONDITION": (
                "Every measured design is equivalent once CEC automation exists."
            ),
            "QOR_PASS_CONDITION": (
                "Provisional AND/depth/runtime deltas are recorded for every "
                "benchmark in scope, including failures and skipped designs."
            ),
        }

    def _failure_artifacts_from_validation(
        self,
        reply: ModelReply,
        issues: tuple[ValidationIssue, ...],
        evidence: Mapping[str, str],
    ) -> AgentArtifacts:
        issue_lines = [
            f"- `{issue.severity}` `{issue.field}`: {issue.message}"
            for issue in issues
        ]
        issue_markdown = "\n".join(issue_lines) + "\n" if issue_lines else "- None.\n"
        raw_preview = reply.raw_text[:2000]
        parsed_keys = sorted(str(key) for key in reply.parsed_json.keys())

        return AgentArtifacts(
            plan_markdown=(
                f"# {self.paper_role} Plan -- {self.context.candidate_id}\n\n"
                "## Status\n\n"
                "Validation failed before a candidate plan was accepted.\n\n"
                "## Evidence Files\n\n"
                f"{_markdown_bullets(evidence.keys())}"
            ),
            candidate_markdown=(
                f"# {self.paper_role} Candidate -- {self.context.candidate_id}\n\n"
                "- Decision: NEEDS_HUMAN_REVIEW\n"
                "- Candidate materialization: not_run\n"
                "- Flow file written: no\n\n"
                "## Parsed JSON Keys\n\n"
                f"{_markdown_bullets(parsed_keys)}"
            ),
            feedback_markdown=(
                f"# {self.paper_role} Feedback -- {self.context.candidate_id}\n\n"
                "## Validation Issues\n\n"
                f"{issue_markdown}\n"
                "## Raw Model Text Preview\n\n"
                "```json\n"
                f"{raw_preview}\n"
                "```\n\n"
                "## Local Status\n\n"
                "- validation_status: failed\n"
                "- decision: NEEDS_HUMAN_REVIEW\n"
                "- `.abc` flow file: not written\n"
            ),
            rule_update_markdown=(
                f"# {self.paper_role} Rule Updates -- {self.context.candidate_id}\n\n"
                "- No active rulebase update was applied.\n"
                "- Validation failed before rule proposals could be accepted.\n"
            ),
            decision="NEEDS_HUMAN_REVIEW",
        )

    def _success_artifacts_from_flow_response(
        self,
        response: FlowAgentResponse,
        evidence: Mapping[str, str],
    ) -> AgentArtifacts:
        compatibility = json.dumps(
            dict(response.compatibility_notes),
            indent=2,
            sort_keys=True,
        )

        return AgentArtifacts(
            plan_markdown=(
                f"# {self.paper_role} Plan -- {self.context.candidate_id}\n\n"
                "## Rationale\n\n"
                f"{response.rationale}\n\n"
                "## Source Design\n\n"
                f"{response.source_design or 'None specified.'}\n\n"
                "## Entry Points\n\n"
                f"{_markdown_bullets(response.entry_points)}\n"
                "## Invariants\n\n"
                f"{_markdown_bullets(response.invariants)}\n"
                "## Risk Hotspots\n\n"
                f"{_markdown_bullets(response.risk_hotspots)}"
            ),
            candidate_markdown=(
                f"# {self.paper_role} Candidate -- {self.context.candidate_id}\n\n"
                f"- Decision: {response.decision}\n"
                f"- Candidate kind: {response.candidate_kind}\n"
                "- Local status: validated_not_materialized\n"
                "- `.abc` flow file: not written in F2\n\n"
                "## Candidate Steps\n\n"
                f"{_markdown_bullets(response.candidate_steps)}\n"
                "## Files To Write Later\n\n"
                f"{_markdown_bullets(response.files_to_write)}\n"
                "## Expected Effect\n\n"
                f"{response.expected_effect}\n\n"
                "## Compatibility Notes\n\n"
                "```json\n"
                f"{compatibility}\n"
                "```\n\n"
                "## Evidence Files\n\n"
                f"{_markdown_bullets(evidence.keys())}"
            ),
            feedback_markdown=(
                f"# {self.paper_role} Feedback -- {self.context.candidate_id}\n\n"
                "## Local Status\n\n"
                "- validation_status: passed\n"
                "- materialization_status: not_run_in_F2\n"
                "- correctness_status: provisional_until_CEC\n\n"
                "## Validation Plan\n\n"
                f"{_markdown_bullets(response.validation_plan)}\n"
                "## Risks\n\n"
                f"{_markdown_bullets(response.risks)}\n"
                "## Rollback Plan\n\n"
                f"{response.rollback_plan}\n"
            ),
            rule_update_markdown=(
                f"# {self.paper_role} Rule Updates -- {self.context.candidate_id}\n\n"
                "Active rulebase was not modified.\n\n"
                "## Proposed Updates\n\n"
                f"{_markdown_bullets(response.rule_updates)}"
            ),
            decision=response.decision,
        )

    def _previous_flow_context(self, previous_cycle: str) -> str:
        outputs = self.context.repo_root / "experiments" / previous_cycle / "outputs"
        preferred = ("epfl_adder", "epfl_bar", "epfl_sqrt")
        paths = tuple(outputs / f"{name}.flowtune.script" for name in preferred)
        return summarize_flow_scripts(paths, max_files=3, max_chars=6000)

    def _runtime_context(self, evidence: Mapping[str, str]) -> str:
        lines = [
            "Flow-only cycle: compile can be skipped unless source edits are introduced.",
            "cycle_000 evidence files loaded:",
            *(f"- {path}" for path in evidence),
        ]
        return compact_text_block("compile_or_runtime_context", "\n".join(lines), 4000)

    def _read_optional_block(self, label: str, path: Path, max_chars: int) -> str:
        if not path.exists():
            return f"{label}: missing"
        return compact_text_block(
            label,
            path.read_text(encoding="utf-8", errors="replace"),
            max_chars=max_chars,
        )

    def _smoke_command(self) -> str:
        flow_path = self.candidate_flow_path()
        return (
            'abc -c "source third_party/FlowTune/abc.rc; '
            "read benchmarks/epfl/epfl_adder.blif; "
            f"source {flow_path.relative_to(self.context.repo_root)}; "
            'strash; ps"'
        )

    def _qor_command(self) -> str:
        flow_path = self.candidate_flow_path().relative_to(self.context.repo_root)
        benchmarks = self.context.assignment.get("benchmark_scope", ())
        joined = ", ".join(str(item) for item in benchmarks)
        return (
            "For each benchmark in scope "
            f"({joined}), run ABC with read <benchmark>; source {flow_path}; "
            "strash; ps; record AND count, depth, runtime, exit status, and skip reason."
        )

    def _validate_rendered_prompt(self, prompt: str) -> None:
        unresolved = find_unresolved_placeholders(prompt)
        if unresolved:
            raise ValueError(
                "unresolved Flow Agent prompt placeholders: "
                + ", ".join(unresolved)
            )

        forbidden = (".env", "API_KEY", "EDA_AGENT_MODEL_API_KEY")
        leaked = [item for item in forbidden if item in prompt]
        if leaked:
            raise ValueError("rendered prompt contains forbidden secret markers.")

        if "TODO_CODING_PROMPT_RENDER" in prompt:
            raise ValueError("Flow Agent prompt still contains scaffold TODO.")


def _markdown_bullets(items: Any) -> str:
    values = [str(item) for item in items if str(item)]
    if not values:
        return "- None.\n"
    return "\n".join(f"- {item}" for item in values) + "\n"
