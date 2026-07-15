"""Planning Agent — LLM-based planner.

Renders ``planner_prompt.md`` with real cycle evidence and calls the model
to produce a next-cycle plan.  Falls back to the deterministic engine when
the model is unavailable or too expensive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.agents.self_evolved_abc.base_agent import PaperAgent
from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import DEFAULT_EVAL_FLOW_COMMANDS
from scripts.agents.self_evolved_abc.model_client import (
    ModelClient,
    ModelConfigurationError,
    ModelInvocation,
    ModelReply,
    TodoModelClient,
    build_model_client_from_env,
)
from scripts.agents.self_evolved_abc.planning.engine import PlanningEngine
from scripts.agents.self_evolved_abc.prompt_rendering import (
    compact_text_block,
    find_forbidden_secret_markers,
    find_unresolved_placeholders,
    load_template,
    render_template,
)
from scripts.agents.self_evolved_abc.repository_context import (
    build_repository_context,
)
from scripts.agents.self_evolved_abc.schemas import AgentArtifacts, markdown_bullets
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    LEGACY_CYCLE_LAYOUT,
    implementation_root_for,
)


class PlanningAgent(PaperAgent):
    """Paper-style Planning Agent.

    Owns cycle objectives, subsystem selection, rollback policy, and global QoR
    interpretation.  Uses the deterministic engine as fallback when the LLM is
    not configured.
    """

    agent_name = "planning_agent"
    paper_role = "Planning Agent"
    prompt_template = "configs/agents/prompts/planner_prompt.md"

    @classmethod
    def create_parallel_coding_dispatch(
        cls,
        *,
        planner_mode: str = "auto",
        model_client: ModelClient | None = None,
        **kwargs: Any,
    ) -> object:
        """Run Planning once, then freeze one Flow and one Logic assignment."""

        from scripts.agents.self_evolved_abc.planning.portfolio import (
            create_portfolio_plan,
        )

        return create_portfolio_plan(
            **kwargs,
            planner_advice_provider=cls._parallel_advice_provider(
                planner_mode=planner_mode,
                model_client=model_client,
            ),
        )

    @classmethod
    def create_next_parallel_coding_dispatch(
        cls,
        *,
        planner_mode: str = "auto",
        model_client: ModelClient | None = None,
        **kwargs: Any,
    ) -> object:
        """Run Planning once after fan-in, then freeze the next paired round."""

        from scripts.agents.self_evolved_abc.planning.portfolio import (
            create_next_portfolio_plan,
        )

        return create_next_portfolio_plan(
            **kwargs,
            planner_advice_provider=cls._parallel_advice_provider(
                planner_mode=planner_mode,
                model_client=model_client,
            ),
        )

    @classmethod
    def _parallel_advice_provider(
        cls,
        *,
        planner_mode: str,
        model_client: ModelClient | None,
    ) -> Callable[[Mapping[str, object]], Mapping[str, object]] | None:
        if planner_mode not in {"auto", "model", "deterministic"}:
            raise ValueError(f"unsupported planner_mode: {planner_mode!r}")
        if planner_mode == "deterministic":
            return None
        client = model_client or build_model_client_from_env()
        if isinstance(client, TodoModelClient):
            if planner_mode == "model":
                raise ModelConfigurationError(
                    "planner model mode requires a configured model provider"
                )
            return None

        def provide(locked: Mapping[str, object]) -> Mapping[str, object]:
            context = cls._planner_context(locked)
            agent = cls(context=context, model_client=client)
            evidence = dict(context.read_evidence_text())
            combined = "\n\n".join(
                f"## {name}\n{text}" for name, text in evidence.items()
            ) or "No prior branch evidence; this is the initial paired round."
            evidence["compile_or_build"] = combined
            evidence["cec_or_correctness"] = combined
            evidence["qor_or_metrics"] = combined
            invocation = agent.build_model_invocation(evidence)
            reply = agent.call_model(invocation)
            agent.materialize_reply(reply, evidence)
            advice = dict(reply.parsed_json)
            advice["source"] = "model"
            return advice

        return provide

    @classmethod
    def _planner_context(cls, locked: Mapping[str, object]) -> CycleContext:
        branches = locked.get("branches")
        if not isinstance(branches, Mapping):
            raise ValueError("locked portfolio is missing branches")
        flow = branches.get("flow")
        if not isinstance(flow, Mapping):
            raise ValueError("locked portfolio is missing Flow assignment")
        assignment = dict(flow)
        evidence: list[str] = []
        for role in ("flow", "logic"):
            branch = branches.get(role)
            if not isinstance(branch, Mapping):
                raise ValueError(f"locked portfolio is missing {role} assignment")
            for value in branch.get("recent_evidence", ()):
                relative = str(value)
                if relative not in evidence:
                    evidence.append(relative)
        assignment.update(
            {
                "agent_name": cls.agent_name,
                "paper_role": cls.paper_role,
                "candidate_id": "planner_dispatch",
                "cycle_id": locked["cycle_id"],
                "previous_cycle_id": locked["previous_cycle_id"],
                "recent_evidence": evidence,
                # Section 3.3 gives the cycle-0 planner the repository-wide
                # profile.  Keep that prior available (but immutable) in later
                # rounds so autonomous planning can refer back to the same
                # pinned knowledge rather than live, drifting web content.
                "repository_context_max_chars": 96_000,
                "repository_context_max_repositories": 10,
                "repository_context_files_per_repository": 2,
                "repository_context_min_available": 10,
                "repository_context_enforce_minimum": True,
                "repository_context_query_terms": [
                    "abc",
                    "flowtune",
                    "rewrite",
                    "resubstitution",
                    "refactoring",
                    "orchestration",
                    "command registration",
                    "module.make",
                    "equivalence",
                    "qor",
                ],
            }
        )
        return CycleContext(
            repo_root=Path(str(locked["repo_root"])),
            assignment=assignment,
        )

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def build_model_invocation(self, evidence: Mapping[str, str]) -> ModelInvocation:
        template = load_template(self.context.repo_root, self.prompt_template)
        values = self._prompt_values(evidence)
        user_prompt = render_template(template, values)
        self._validate_rendered_prompt(user_prompt)

        system_prompt = (
            "You are the Planning Agent for a small reproduction of "
            "Multi-Agent Self-Evolved ABC. Propose one conservative round "
            "objective and exactly two isolated coding dispatches: one Flow "
            "Agent and one Logic Minimization Agent. The candidate identities, "
            "source ownership, baseline, benchmark scope, and evaluation flow "
            "shown in the prompt are locked. Return exactly one JSON object and "
            "do not include Markdown prose."
        )

        return ModelInvocation(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=self.response_schema(),
            temperature=0.0,
        )

    def response_schema(self) -> Mapping[str, Any]:
        dispatch_schema = {
            "type": "object",
            "required": [
                "branch_role",
                "agent_name",
                "candidate_id",
                "task_type",
                "hypothesis",
                "coding_agent_task",
                "source_patch_mode",
                "source_patch_allowed_roots",
                "acceptance_criteria",
                "rollback_criteria",
            ],
            "additionalProperties": False,
            "properties": {
                "branch_role": {"type": "string", "enum": ["flow", "logic"]},
                "agent_name": {
                    "type": "string",
                    "enum": ["flow_agent", "logic_minimization_agent"],
                },
                "candidate_id": {"type": "string"},
                "task_type": {
                    "type": "string",
                    "enum": ["optimization", "repair", "instrumentation"],
                },
                "hypothesis": {"type": "string"},
                "coding_agent_task": {"type": "string"},
                "source_patch_mode": {
                    "type": "string",
                    "enum": ["source_patch_diff"],
                },
                "source_patch_allowed_roots": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rollback_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        return {
            "type": "object",
            "required": [
                "cycle_objective",
                "dispatches",
                "benchmark_scope",
                "evaluation_flow_commands",
                "risk_controls",
            ],
            "additionalProperties": False,
            "properties": {
                "cycle_objective": {"type": "string"},
                "dispatches": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": dispatch_schema,
                },
                "benchmark_scope": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "allowed_to_read": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evaluation_flow_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "evidence_summary": {
                    "type": "object",
                    "properties": {
                        "compile": {"type": "string"},
                        "cec": {"type": "string"},
                        "qor": {"type": "string"},
                        "runtime": {"type": "string"},
                    },
                },
                "validation_evidence": {"type": "object"},
                "risk_controls": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rulebase_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }

    def materialize_reply(
        self, reply: ModelReply, evidence: Mapping[str, str]
    ) -> AgentArtifacts:
        data = reply.parsed_json
        objective = str(data.get("cycle_objective", ""))
        dispatches = [
            item for item in data.get("dispatches", ()) if isinstance(item, Mapping)
        ]
        self._validate_dual_dispatches(dispatches)
        risk_controls = [
            str(item)
            for item in data.get("risk_controls", ())
            if str(item)
        ]
        rulebase_notes = [
            str(item)
            for item in data.get("rulebase_notes", ())
            if str(item)
        ]

        evidence_summary = data.get("evidence_summary", {}) or {}
        compile_status = str(evidence_summary.get("compile", "missing"))
        cec_status = str(evidence_summary.get("cec", "missing"))
        qor_status = str(evidence_summary.get("qor", "inconclusive"))
        dispatch_plan = self._render_dispatches(dispatches, include_tasks=False)
        dispatch_tasks = self._render_dispatches(dispatches, include_tasks=True)

        return AgentArtifacts(
            plan_markdown=(
                f"# Planning Agent Plan -- {self.context.candidate_id}\n\n"
                f"## Objective\n\n{objective}\n\n"
                f"## Parallel Coding Dispatches\n\n{dispatch_plan}"
                f"## Evidence Summary\n\n"
                f"- compile: {compile_status}\n"
                f"- cec: {cec_status}\n"
                f"- qor: {qor_status}\n\n"
                f"## Risk Controls\n\n{markdown_bullets(risk_controls)}"
            ),
            candidate_markdown=(
                "# Planner Parallel Candidate Dispatch\n\n"
                f"{dispatch_tasks}"
                f"- Evidence files read: {', '.join(evidence.keys())}\n"
            ),
            feedback_markdown=(
                "# Planning Feedback\n\n"
                "Planning Agent produced one frozen Flow/Logic dispatch. "
                "Both branch reviews must settle before the next round.\n"
            ),
            rule_update_markdown=(
                "# Rulebase Update Proposal\n\n"
                f"{markdown_bullets(rulebase_notes)}"
            ),
            decision="PROPOSE_CANDIDATES",
        )

    # ------------------------------------------------------------------
    # Deterministic fallback
    # ------------------------------------------------------------------

    def plan_deterministic(self) -> AgentArtifacts:
        """Run the deterministic engine instead of calling the LLM.

        Useful when the model is not configured, token budget is exhausted,
        or a quick plan is needed before the remote run.
        """
        engine = PlanningEngine(self.context.repo_root)
        result = engine.plan(self.context.cycle_id)
        if result is None:
            return AgentArtifacts(
                plan_markdown=(
                    "# Planning Agent Plan — fallback\n\n"
                    "No previous cycle evidence found. Use the default "
                    "first-cycle Flow Agent assignment targeting csweep.\n"
                ),
                candidate_markdown=(
                    "# Planner Candidate Dispatch — fallback\n\n"
                    "- Selected agent: flow_agent\n"
                    "- Task type: optimization (first cycle)\n"
                    "- Target: csweep cut/leaf floors in Csw_Sweep\n"
                ),
                feedback_markdown=(
                    "# Planning Feedback\n\n"
                    "Deterministic engine fallback — no LLM call made.\n"
                ),
                rule_update_markdown=(
                    "# Rulebase Update Proposal\n\n- None.\n"
                ),
                decision="PROPOSE_CANDIDATE",
            )

        return AgentArtifacts(
            plan_markdown=(
                f"# Planning Agent Plan — deterministic\n\n"
                f"## Objective\n\n{result.hypothesis}\n\n"
                f"## Selected Coding Agent\n\n- flow_agent\n\n"
                f"## Task Type\n\n- {result.strategy.task_type}\n\n"
                f"## Thresholds\n\n"
                f"- zero AND-regressed rows\n"
                f"- improved >= {result.thresholds.min_improved_benchmarks}\n"
                f"- either avg >= {result.thresholds.min_average_and_improve_pct:.1f}% "
                f"or total reduction >= {result.thresholds.min_total_and_reduction}\n"
            ),
            candidate_markdown=(
                "# Planner Candidate Dispatch — deterministic\n\n"
                f"- Selected agent: flow_agent\n"
                f"- Task type: {result.strategy.task_type}\n"
                f"- Target command: {result.strategy.target_command}\n"
                f"- Target source dir: {result.strategy.target_source_dir}\n"
                f"- Skip LLM: {result.strategy.should_skip_llm}\n\n"
                f"## Hypothesis\n\n{result.hypothesis}\n"
            ),
            feedback_markdown=(
                "# Planning Feedback\n\n"
                "Deterministic engine used — no LLM call made.\n"
                f"Strategy rationale: {result.strategy.rationale}\n"
            ),
            rule_update_markdown=(
                "# Rulebase Update Proposal\n\n"
                f"- Threshold adjustment: {result.thresholds.adjustment_reason}\n"
            ),
            decision="PROPOSE_CANDIDATE",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_dual_dispatches(
        self,
        dispatches: list[Mapping[str, Any]],
    ) -> None:
        expected = {
            "flow": (
                "flow_agent",
                "flow_candidate_001",
                "third_party/FlowTune/src/src/opt",
            ),
            "logic": (
                "logic_minimization_agent",
                "logic_candidate_001",
                "third_party/FlowTune/src/src/base/abci",
            ),
        }
        if len(dispatches) != 2:
            raise ValueError("Planning Agent must dispatch exactly Flow and Logic")
        if [str(item.get("branch_role", "")) for item in dispatches] != [
            "flow",
            "logic",
        ]:
            raise ValueError("Planning Agent dispatch order must be Flow then Logic")
        for item in dispatches:
            role = str(item.get("branch_role", ""))
            agent_name, candidate_id, allowed_root = expected[role]
            if item.get("agent_name") != agent_name:
                raise ValueError(f"{role} dispatch agent_name mismatch")
            if item.get("candidate_id") != candidate_id:
                raise ValueError(f"{role} dispatch candidate_id mismatch")
            if item.get("source_patch_mode") != "source_patch_diff":
                raise ValueError(f"{role} dispatch source_patch_mode mismatch")
            roots = [str(value) for value in item.get("source_patch_allowed_roots", ())]
            if roots != [allowed_root]:
                raise ValueError(f"{role} dispatch source ownership mismatch")
            if str(item.get("task_type", "")) not in {
                "optimization",
                "repair",
                "instrumentation",
            }:
                raise ValueError(f"{role} dispatch task_type is not executable")
            for key in ("hypothesis", "coding_agent_task"):
                text = str(item.get(key, "")).strip()
                if not text or len(text) > 8000:
                    raise ValueError(f"{role} dispatch {key} is invalid")
            for key in ("acceptance_criteria", "rollback_criteria"):
                values = item.get(key)
                if (
                    not isinstance(values, (list, tuple))
                    or not values
                    or any(not str(value).strip() for value in values)
                ):
                    raise ValueError(f"{role} dispatch {key} is invalid")

    def _render_dispatches(
        self,
        dispatches: list[Mapping[str, Any]],
        *,
        include_tasks: bool,
    ) -> str:
        lines: list[str] = []
        for item in dispatches:
            role = str(item["branch_role"])
            lines.extend(
                (
                    f"### {role.title()} branch",
                    "",
                    f"- Agent: `{item['agent_name']}`",
                    f"- Candidate: `{item['candidate_id']}`",
                    f"- Task type: `{item['task_type']}`",
                    f"- Hypothesis: {item['hypothesis']}",
                )
            )
            if include_tasks:
                lines.extend(
                    (
                        f"- Task: {item['coding_agent_task']}",
                        "- Allowed source roots: "
                        + ", ".join(
                            f"`{value}`"
                            for value in item.get("source_patch_allowed_roots", ())
                        ),
                    )
                )
            lines.extend(
                (
                    "- Acceptance criteria:\n"
                    + markdown_bullets(item.get("acceptance_criteria", ())).rstrip(),
                    "- Rollback criteria:\n"
                    + markdown_bullets(item.get("rollback_criteria", ())).rstrip(),
                    "",
                )
            )
        return "\n".join(lines) + "\n"

    def _prompt_values(self, evidence: Mapping[str, str]) -> dict[str, object]:
        assignment = self.context.assignment
        repo_root = self.context.repo_root
        previous_cycle = str(assignment.get("previous_cycle_id", "cycle_000"))

        compile_feedback = evidence.get(
            "compile_or_build", "No compile/build evidence provided."
        )
        cec_feedback = evidence.get(
            "cec_or_correctness", "No CEC evidence provided."
        )
        qor_feedback = evidence.get(
            "qor_or_metrics", "No QoR evidence provided."
        )

        champion_summary = self._champion_summary()
        repository_context = build_repository_context(
            repo_root,
            assignment,
            role=self.agent_name,
        )
        minimum_context = int(
            assignment.get("repository_context_min_available", 0)
        )
        if (
            bool(assignment.get("repository_context_enforce_minimum", False))
            and repository_context.available_count < minimum_context
        ):
            raise ValueError(
                "Planning Agent repository context is incomplete: "
                f"{repository_context.available_count}/{minimum_context} pinned "
                "repositories are ready; run scripts/bootstrap_agent_context.py"
            )

        return {
            "REPO_ROOT": str(repo_root),
            "CYCLE_ID": self.context.cycle_id,
            "MODE": "candidate_generation",
            "TIME_BUDGET": "2-3 hours per cycle (remote Linux host)",
            "COMPUTE_BUDGET": "single machine, sequential evaluation",
            "REMOTE_OR_LOCAL": "remote — ABC build, CEC, and QoR run on Linux host",
            "ABC_BINARY": str(
                assignment.get("baseline_abc_bin", "third_party/FlowTune/abc")
            ),
            "PRIOR_KNOWLEDGE_CONTEXT": repository_context.text,
            "CURRENT_CHAMPION_SUMMARY": champion_summary,
            "COMPILE_FEEDBACK": compact_text_block(
                "compile", str(compile_feedback), max_chars=4000
            ),
            "CEC_FEEDBACK": compact_text_block(
                "cec", str(cec_feedback), max_chars=4000
            ),
            "QOR_FEEDBACK": compact_text_block(
                "qor", str(qor_feedback), max_chars=8000
            ),
            "RUNTIME_FEEDBACK": "Runtime data from remote Linux host.",
            "REJECTED_CANDIDATES": self._rejected_candidates_text(previous_cycle),
            "PRIMARY_METRIC": assignment.get(
                "target_metric", "AND node count"
            ),
            "BENCHMARK_SUITES": str(
                assignment.get("benchmark_scope", "EPFL + ISCAS85 + ISCAS89")
            ),
            "FLOW_CONFIGS": str(
                assignment.get(
                    "evaluation_flow_commands",
                    list(DEFAULT_EVAL_FLOW_COMMANDS),
                )
            ),
            "RULEBASE": load_template(
                repo_root, "configs/agents/shared/rulebase.md"
            ),
        }

    def _champion_summary(self) -> str:
        assignment = self.context.assignment
        champion_cycle = assignment.get("champion_cycle_id", "")
        if not champion_cycle or assignment.get("baseline_kind") == "vanilla":
            return (
                "No champion yet — using vanilla ABC/FlowTune binary as baseline. "
                "The first champion will be promoted when a candidate passes "
                "build, CEC, and QoR thresholds."
            )
        return (
            f"Champion from {champion_cycle} "
            f"(candidate {assignment.get('champion_candidate_id', '')}). "
            f"Source root: {assignment.get('champion_source_root', '')}. "
            f"Binary: {assignment.get('champion_abc_bin', '')}."
        )

    def _rejected_candidates_text(self, previous_cycle: str) -> str:
        review_path = (
            implementation_root_for(
                repo_root=self.context.repo_root,
                cycle_id=previous_cycle,
                candidate_id=str(
                    self.context.assignment.get(
                        "champion_candidate_id", self.context.candidate_id
                    )
                ),
                layout=str(
                    self.context.assignment.get(
                        "artifact_layout", LEGACY_CYCLE_LAYOUT
                    )
                ),
            )
            / "comparison"
            / "review_decision.json"
        )
        if not review_path.is_file():
            return "No rejected candidates yet."

        import json

        try:
            payload = json.loads(review_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return "Could not read review decision."

        decision = str(payload.get("decision", "missing"))
        if decision == "ACCEPT_FOR_NEXT_CYCLE":
            return f"Previous candidate ({previous_cycle}) was ACCEPTED — no rejections."
        return (
            f"Previous candidate ({previous_cycle}) was {decision}. "
            f"Reason: {payload.get('reason', 'unknown')}. "
            f"Next action: {payload.get('next_action', 'unknown')}."
        )

    def _validate_rendered_prompt(self, prompt: str) -> None:
        unresolved = find_unresolved_placeholders(prompt)
        if unresolved:
            raise ValueError(
                "unresolved Planning Agent prompt placeholders: "
                + ", ".join(unresolved)
            )

        leaked = find_forbidden_secret_markers(prompt)
        if leaked:
            raise ValueError(
                "rendered prompt contains forbidden secret markers."
            )

        if "TODO_PLANNER_PROMPT_RENDER" in prompt:
            raise ValueError(
                "Planning Agent prompt still contains scaffold TODO."
            )
