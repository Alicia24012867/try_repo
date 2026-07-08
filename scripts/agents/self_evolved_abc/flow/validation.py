"""Validation helpers for model-generated Flow Agent responses."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_ABC_FLOW,
    FLOW_CANDIDATE_DIAGNOSTIC_ONLY,
    FLOW_CANDIDATE_KINDS,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_CANDIDATE_SOURCE_PATCH_TODO,
    FLOW_DECISIONS,
    FLOW_SOURCE_PATCH_DIFF_ALLOWED_ROOTS,
    FLOW_SOURCE_PATCH_TODO_ALLOWED_ROOTS,
)
from scripts.agents.self_evolved_abc.schemas import (
    FlowAgentResponse,
    ValidationIssue,
    ValidationResult,
)


FLOW_REQUIRED_FIELDS: tuple[str, ...] = (
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
)

FLOW_RESPONSE_JSON_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "required": list(FLOW_REQUIRED_FIELDS),
    "additionalProperties": False,
    "properties": {
        "rationale": {"type": "string"},
        "candidate_kind": {
            "type": "string",
            "enum": list(FLOW_CANDIDATE_KINDS),
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
        "source_patch": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "patch_format": {"type": "string"},
                "target_scope": {"type": "string"},
                "apply_strategy": {"type": "string"},
                "diff": {"type": "string"},
            },
        },
        "decision": {
            "type": "string",
            "enum": list(FLOW_DECISIONS),
        },
    },
}

FORBIDDEN_ABC_STEP_SUBSTRINGS: tuple[str, ...] = (
    "&&",
    "||",
    "|",
    ">",
    "<",
    "`",
    "$(",
)

FORBIDDEN_ABC_STEP_COMMANDS: tuple[str, ...] = (
    "bash",
    "cat",
    "cp",
    "curl",
    "make",
    "mv",
    "python",
    "python3",
    "rm",
    "sh",
    "wget",
)

FORBIDDEN_FLOW_IO_COMMAND_PREFIXES: tuple[str, ...] = (
    "read",
    "write",
    "write_aiger",
    "write_blif",
)

FORBIDDEN_SOURCE_PATCH_STEP_SUBSTRINGS: tuple[str, ...] = (
    "&&",
    "||",
    "$(",
)

FORBIDDEN_SOURCE_PATCH_STEP_COMMANDS: tuple[str, ...] = FORBIDDEN_ABC_STEP_COMMANDS


def flow_response_json_schema() -> Mapping[str, Any]:
    """Return the Flow Agent response schema used for model JSON mode."""

    return deepcopy(FLOW_RESPONSE_JSON_SCHEMA)


def validate_flow_agent_response(
    data: Mapping[str, Any],
    context: CycleContext,
) -> ValidationResult:
    """Validate one Flow Agent model JSON response."""

    missing_issues = require_keys(data, FLOW_REQUIRED_FIELDS)
    if missing_issues:
        return _failed(missing_issues)

    rationale, issues = expect_string(data, "rationale")
    all_issues = list(issues)
    candidate_kind, issues = expect_enum(
        data, "candidate_kind", FLOW_CANDIDATE_KINDS
    )
    all_issues.extend(issues)
    candidate_steps, issues = expect_list_of_strings(data, "candidate_steps")
    all_issues.extend(issues)
    source_design, issues = expect_string(data, "source_design", allow_empty=True)
    all_issues.extend(issues)
    expected_effect, issues = expect_string(data, "expected_effect")
    all_issues.extend(issues)
    entry_points, issues = expect_list_of_strings(data, "entry_points")
    all_issues.extend(issues)
    invariants, issues = expect_list_of_strings(data, "invariants")
    all_issues.extend(issues)
    risk_hotspots, issues = expect_list_of_strings(data, "risk_hotspots")
    all_issues.extend(issues)
    files_to_write, issues = expect_list_of_strings(data, "files_to_write")
    all_issues.extend(issues)
    compatibility_notes, issues = expect_mapping(data, "compatibility_notes")
    all_issues.extend(issues)
    validation_plan, issues = expect_list_of_strings(
        data, "validation_plan", allow_empty=False
    )
    all_issues.extend(issues)
    risks, issues = expect_list_of_strings(data, "risks")
    all_issues.extend(issues)
    rollback_plan, issues = expect_string(data, "rollback_plan")
    all_issues.extend(issues)
    rule_updates, issues = expect_list_of_strings(data, "rule_updates")
    all_issues.extend(issues)
    decision, issues = expect_enum(data, "decision", FLOW_DECISIONS)
    all_issues.extend(issues)
    source_patch, issues = expect_optional_mapping(data, "source_patch")
    all_issues.extend(issues)

    if not all_issues:
        all_issues.extend(
            validate_assignment_mode_matches_candidate_kind(
                candidate_kind=candidate_kind or "",
                decision=decision or "",
                context=context,
            )
        )

    if not all_issues:
        normalized_steps, issues = validate_candidate_steps(
            candidate_steps or (),
            candidate_kind=candidate_kind or "",
            benchmark_scope=context.benchmark_scope,
        )
        all_issues.extend(issues)
    else:
        normalized_steps = candidate_steps

    if not all_issues:
        _, issues = validate_files_to_write(
            files_to_write or (),
            context,
            candidate_kind=candidate_kind or "",
        )
        all_issues.extend(issues)

    if not all_issues:
        all_issues.extend(
            validate_source_patch_payload(
                candidate_kind=candidate_kind or "",
                source_patch=source_patch,
                context=context,
                files_to_write=files_to_write or (),
            )
        )

    if not all_issues:
        all_issues.extend(
            validate_decision_semantics(
                decision=decision or "",
                candidate_kind=candidate_kind or "",
                candidate_steps=normalized_steps or (),
                files_to_write=files_to_write or (),
                entry_points=entry_points or (),
                invariants=invariants or (),
                rationale=rationale or "",
                validation_plan=validation_plan or (),
                rollback_plan=rollback_plan or "",
            )
        )

    if all_issues:
        return _failed(tuple(all_issues))

    response = FlowAgentResponse(
        rationale=rationale or "",
        candidate_kind=candidate_kind or "",
        candidate_steps=normalized_steps or (),
        source_design=source_design or "",
        expected_effect=expected_effect or "",
        entry_points=entry_points or (),
        invariants=invariants or (),
        risk_hotspots=risk_hotspots or (),
        files_to_write=files_to_write or (),
        compatibility_notes=compatibility_notes or {},
        validation_plan=validation_plan or (),
        risks=risks or (),
        rollback_plan=rollback_plan or "",
        rule_updates=rule_updates or (),
        decision=decision or "NEEDS_HUMAN_REVIEW",
        source_patch=source_patch,
    )
    return ValidationResult(
        ok=True,
        response=response,
        issues=(),
        decision=response.decision,
    )


def require_keys(
    data: Mapping[str, Any],
    required: tuple[str, ...],
) -> tuple[ValidationIssue, ...]:
    """Return missing-key issues."""

    return tuple(
        ValidationIssue(field=field, message="missing required field")
        for field in required
        if field not in data
    )


def expect_string(
    data: Mapping[str, Any],
    field: str,
    *,
    allow_empty: bool = False,
) -> tuple[str | None, tuple[ValidationIssue, ...]]:
    """Read a string field."""

    value = data.get(field)
    if not isinstance(value, str):
        return None, (_type_issue(field, "string"),)

    normalized = value.strip()
    if not allow_empty and not normalized:
        return None, (ValidationIssue(field=field, message="must not be empty"),)
    return normalized, ()


def expect_list_of_strings(
    data: Mapping[str, Any],
    field: str,
    *,
    allow_empty: bool = True,
) -> tuple[tuple[str, ...] | None, tuple[ValidationIssue, ...]]:
    """Read a list[str] field."""

    value = data.get(field)
    if not isinstance(value, list):
        return None, (_type_issue(field, "list[string]"),)

    issues: list[ValidationIssue] = []
    strings: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            issues.append(
                ValidationIssue(
                    field=f"{field}[{index}]",
                    message="must be a string",
                )
            )
            continue
        normalized = item.strip()
        if not normalized:
            issues.append(
                ValidationIssue(
                    field=f"{field}[{index}]",
                    message="must not be empty",
                )
            )
            continue
        strings.append(normalized)

    if not allow_empty and not strings:
        issues.append(ValidationIssue(field=field, message="must not be empty"))

    return tuple(strings), tuple(issues)


def expect_enum(
    data: Mapping[str, Any],
    field: str,
    allowed: tuple[str, ...],
) -> tuple[str | None, tuple[ValidationIssue, ...]]:
    """Read a string enum field."""

    value, issues = expect_string(data, field)
    if issues or value is None:
        return None, issues
    if value not in allowed:
        return None, (
            ValidationIssue(
                field=field,
                message=f"must be one of: {', '.join(allowed)}",
            ),
        )
    return value, ()


def expect_mapping(
    data: Mapping[str, Any],
    field: str,
) -> tuple[Mapping[str, Any] | None, tuple[ValidationIssue, ...]]:
    """Read an object/dict field."""

    value = data.get(field)
    if not isinstance(value, Mapping):
        return None, (_type_issue(field, "object"),)
    return dict(value), ()


def expect_optional_mapping(
    data: Mapping[str, Any],
    field: str,
) -> tuple[Mapping[str, Any] | None, tuple[ValidationIssue, ...]]:
    """Read an optional object/dict field."""

    if field not in data or data.get(field) is None:
        return None, ()
    return expect_mapping(data, field)


def validate_assignment_mode_matches_candidate_kind(
    *,
    candidate_kind: str,
    decision: str,
    context: CycleContext,
) -> tuple[ValidationIssue, ...]:
    """Ensure materialized candidates obey the assignment-selected mode."""

    if decision != "PROPOSE_CANDIDATE":
        return ()

    mode = str(context.assignment.get("source_patch_mode", "")).strip()
    if not mode:
        return ()

    expected_by_mode = {
        FLOW_CANDIDATE_ABC_FLOW: FLOW_CANDIDATE_ABC_FLOW,
        FLOW_CANDIDATE_SOURCE_PATCH_DIFF: FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        FLOW_CANDIDATE_SOURCE_PATCH_TODO: FLOW_CANDIDATE_SOURCE_PATCH_TODO,
    }
    expected = expected_by_mode.get(mode)
    if expected is None:
        return (
            ValidationIssue(
                "source_patch_mode",
                "assignment source_patch_mode must be one of: "
                + ", ".join(sorted(expected_by_mode)),
            ),
        )

    if candidate_kind != expected:
        return (
            ValidationIssue(
                "candidate_kind",
                f"assignment source_patch_mode={mode!r} requires "
                f"candidate_kind={expected!r}, got {candidate_kind!r}",
            ),
        )
    return ()


def validate_path_in_allowed_scope(
    relative_path: str,
    *,
    repo_root: Path,
    allowed_roots: tuple[str, ...],
    field: str = "files_to_write",
    scope_label: str = "allowed Flow Agent write scope",
) -> tuple[Path | None, tuple[ValidationIssue, ...]]:
    """Check that a model-proposed path stays inside allowed repo scope."""

    path_text = relative_path.strip()
    if not path_text:
        return None, (ValidationIssue(field, "path must not be empty"),)

    candidate_path = Path(path_text)
    if candidate_path.is_absolute():
        return None, (
            ValidationIssue(field, f"absolute path is not allowed: {path_text}"),
        )

    resolved_root = repo_root.resolve()
    resolved_path = (resolved_root / candidate_path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return None, (
            ValidationIssue(field, f"path escapes repository: {path_text}"),
        )

    allowed_paths = tuple((resolved_root / root).resolve() for root in allowed_roots)
    for allowed_path in allowed_paths:
        if _path_is_under_or_equal(resolved_path, allowed_path):
            return resolved_path, ()

    return None, (
        ValidationIssue(
            field,
            f"path is outside {scope_label}: {path_text}",
        ),
    )


def validate_files_to_write(
    files_to_write: tuple[str, ...],
    context: CycleContext,
    *,
    candidate_kind: str,
) -> tuple[tuple[Path, ...] | None, tuple[ValidationIssue, ...]]:
    """Validate every requested output path."""

    candidate_allowed_roots = _candidate_kind_allowed_roots(context, candidate_kind)
    assignment_allowed_roots = _assignment_allowed_roots(context)
    issues: list[ValidationIssue] = []
    resolved_paths: list[Path] = []

    for index, relative_path in enumerate(files_to_write):
        resolved_path, path_issues = validate_path_in_allowed_scope(
            relative_path,
            repo_root=context.repo_root,
            allowed_roots=candidate_allowed_roots,
            field=f"files_to_write[{index}]",
            scope_label=f"Flow Agent {candidate_kind} write scope",
        )
        issues.extend(path_issues)
        if resolved_path is None:
            continue

        if assignment_allowed_roots:
            _, assignment_issues = validate_path_in_allowed_scope(
                relative_path,
                repo_root=context.repo_root,
                allowed_roots=assignment_allowed_roots,
                field=f"files_to_write[{index}]",
                scope_label="assignment allowed_to_edit",
            )
            issues.extend(assignment_issues)
            if assignment_issues:
                continue

        resolved_paths.append(resolved_path)

    if issues:
        return None, tuple(issues)
    return tuple(resolved_paths), ()


def _candidate_kind_allowed_roots(
    context: CycleContext,
    candidate_kind: str,
) -> tuple[str, ...]:
    active_agent_dir = f"experiments/{context.cycle_id}/agents"
    if candidate_kind == FLOW_CANDIDATE_ABC_FLOW:
        return ("configs/flows", active_agent_dir)
    if candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_TODO:
        return (*FLOW_SOURCE_PATCH_TODO_ALLOWED_ROOTS, active_agent_dir)
    if candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
        return (*FLOW_SOURCE_PATCH_DIFF_ALLOWED_ROOTS, active_agent_dir)
    if candidate_kind == FLOW_CANDIDATE_DIAGNOSTIC_ONLY:
        return (active_agent_dir,)
    return ()


def _assignment_allowed_roots(context: CycleContext) -> tuple[str, ...]:
    return tuple(
        str(item).strip()
        for item in context.assignment.get("allowed_to_edit", ())
        if str(item).strip()
    )


def _path_is_under_or_equal(path: Path, allowed_root: Path) -> bool:
    if path == allowed_root:
        return True
    try:
        path.relative_to(allowed_root)
    except ValueError:
        return False
    return True


def normalize_abc_command(command: str) -> str:
    """Strip whitespace and trailing semicolons from one ABC command."""

    return command.strip().rstrip(";").strip()


def validate_abc_command(
    command: str,
    *,
    benchmark_scope: tuple[str, ...],
) -> tuple[str | None, tuple[ValidationIssue, ...]]:
    """Validate one candidate_steps entry as an ABC command."""

    normalized = normalize_abc_command(command)
    if not normalized:
        return None, (ValidationIssue("candidate_steps", "command must not be empty"),)

    issues: list[ValidationIssue] = []
    for forbidden in FORBIDDEN_ABC_STEP_SUBSTRINGS:
        if forbidden in normalized:
            issues.append(
                ValidationIssue(
                    "candidate_steps",
                    f"command contains forbidden shell syntax {forbidden!r}: {normalized}",
                )
            )

    if ";" in normalized:
        issues.append(
            ValidationIssue(
                "candidate_steps",
                f"candidate_steps entries must contain one ABC command, not a command list: {normalized}",
            )
        )

    command_name = normalized.split(maxsplit=1)[0]
    if command_name in FORBIDDEN_ABC_STEP_COMMANDS:
        issues.append(
            ValidationIssue(
                "candidate_steps",
                f"shell command is not an ABC flow command: {normalized}",
            )
        )

    if any(
        command_name == prefix or command_name.startswith(f"{prefix}_")
        for prefix in FORBIDDEN_FLOW_IO_COMMAND_PREFIXES
    ):
        issues.append(
            ValidationIssue(
                "candidate_steps",
                f"flow candidate must not perform benchmark IO: {normalized}",
            )
        )

    hard_coded_names = _benchmark_hard_code_tokens(benchmark_scope)
    lower_command = normalized.lower()
    for token in hard_coded_names:
        if token in lower_command:
            issues.append(
                ValidationIssue(
                    "candidate_steps",
                    f"command hard-codes benchmark or design token {token!r}: {normalized}",
                )
            )

    if issues:
        return None, tuple(issues)
    return normalized, ()


def validate_candidate_steps(
    candidate_steps: tuple[str, ...],
    *,
    candidate_kind: str,
    benchmark_scope: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, tuple[ValidationIssue, ...]]:
    """Validate candidate steps according to candidate kind."""

    if candidate_kind == FLOW_CANDIDATE_ABC_FLOW:
        return validate_abc_flow_steps(
            candidate_steps,
            benchmark_scope=benchmark_scope,
        )
    if candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_TODO:
        return validate_source_patch_steps(
            candidate_steps,
            benchmark_scope=benchmark_scope,
        )
    if candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
        return validate_source_patch_steps(
            candidate_steps,
            benchmark_scope=benchmark_scope,
            candidate_kind=FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        )
    if candidate_kind == FLOW_CANDIDATE_DIAGNOSTIC_ONLY:
        return tuple(step.strip() for step in candidate_steps if step.strip()), ()
    return None, (
        ValidationIssue(
            "candidate_kind",
            f"unsupported Flow candidate kind: {candidate_kind}",
        ),
    )


def validate_abc_flow_steps(
    candidate_steps: tuple[str, ...],
    *,
    benchmark_scope: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, tuple[ValidationIssue, ...]]:
    """Validate all ABC flow commands."""

    if not candidate_steps:
        return None, (
            ValidationIssue(
                "candidate_steps",
                "candidate_kind=abc_flow requires at least one ABC command",
            ),
        )

    issues: list[ValidationIssue] = []
    normalized_steps: list[str] = []
    for index, command in enumerate(candidate_steps):
        normalized, command_issues = validate_abc_command(
            command,
            benchmark_scope=benchmark_scope,
        )
        for issue in command_issues:
            issues.append(
                ValidationIssue(
                    field=f"{issue.field}[{index}]",
                    message=issue.message,
                    severity=issue.severity,
                )
            )
        if normalized is not None:
            normalized_steps.append(normalized)

    if issues:
        return None, tuple(issues)
    return tuple(normalized_steps), ()


def validate_source_patch_steps(
    candidate_steps: tuple[str, ...],
    *,
    benchmark_scope: tuple[str, ...],
    candidate_kind: str = FLOW_CANDIDATE_SOURCE_PATCH_TODO,
) -> tuple[tuple[str, ...] | None, tuple[ValidationIssue, ...]]:
    """Validate source patch proposal steps without treating them as ABC commands."""

    if not candidate_steps:
        return None, (
            ValidationIssue(
                "candidate_steps",
                f"candidate_kind={candidate_kind} requires a patch plan",
            ),
        )

    issues: list[ValidationIssue] = []
    normalized_steps: list[str] = []
    hard_coded_names = _benchmark_hard_code_tokens(benchmark_scope)
    for index, step in enumerate(candidate_steps):
        normalized = step.strip()
        if not normalized:
            issues.append(
                ValidationIssue(
                    field=f"candidate_steps[{index}]",
                    message="patch plan step must not be empty",
                )
            )
            continue

        lower_step = normalized.lower()
        for forbidden in FORBIDDEN_SOURCE_PATCH_STEP_SUBSTRINGS:
            if forbidden in normalized:
                issues.append(
                    ValidationIssue(
                        field=f"candidate_steps[{index}]",
                        message=(
                            f"{candidate_kind} step contains forbidden shell "
                            f"syntax {forbidden!r}: {normalized}"
                        ),
                    )
                )

        first_word = lower_step.split(maxsplit=1)[0]
        if first_word in FORBIDDEN_SOURCE_PATCH_STEP_COMMANDS:
            issues.append(
                ValidationIssue(
                    field=f"candidate_steps[{index}]",
                    message=(
                        f"{candidate_kind} must describe a patch plan, not a "
                        f"shell command: {normalized}"
                    ),
                )
            )

        for token in hard_coded_names:
            if token in lower_step:
                issues.append(
                    ValidationIssue(
                    field=f"candidate_steps[{index}]",
                    message=(
                        f"{candidate_kind} must not specialize the patch "
                        f"to benchmark/design token {token!r}: {normalized}"
                    ),
                )
                )

        normalized_steps.append(normalized)

    if issues:
        return None, tuple(issues)
    return tuple(normalized_steps), ()


def validate_decision_semantics(
    *,
    decision: str,
    candidate_kind: str,
    candidate_steps: tuple[str, ...],
    files_to_write: tuple[str, ...],
    entry_points: tuple[str, ...],
    invariants: tuple[str, ...],
    rationale: str,
    validation_plan: tuple[str, ...],
    rollback_plan: str,
) -> tuple[ValidationIssue, ...]:
    """Validate consistency between decision and candidate payload."""

    issues: list[ValidationIssue] = []
    if decision == "PROPOSE_CANDIDATE":
        if candidate_kind not in (
            FLOW_CANDIDATE_ABC_FLOW,
            FLOW_CANDIDATE_SOURCE_PATCH_TODO,
            FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
        ):
            issues.append(
                ValidationIssue(
                    "candidate_kind",
                    "PROPOSE_CANDIDATE requires abc_flow, source_patch_todo, or source_patch_diff",
                )
            )
        if not candidate_steps:
            issues.append(
                ValidationIssue(
                    "candidate_steps",
                    "PROPOSE_CANDIDATE requires non-empty candidate_steps",
                )
            )
        if not validation_plan:
            issues.append(
                ValidationIssue(
                    "validation_plan",
                    "PROPOSE_CANDIDATE requires a validation plan",
                )
            )
        if candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_TODO:
            issues.extend(
                validate_source_patch_todo_semantics(
                    files_to_write=files_to_write,
                    entry_points=entry_points,
                    invariants=invariants,
                    validation_plan=validation_plan,
                    rollback_plan=rollback_plan,
                )
            )
        if candidate_kind == FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
            issues.extend(
                validate_source_patch_todo_semantics(
                    files_to_write=files_to_write,
                    entry_points=entry_points,
                    invariants=invariants,
                    validation_plan=validation_plan,
                    rollback_plan=rollback_plan,
                    label=FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
                )
            )
    elif decision == "DEFER":
        if not rationale:
            issues.append(
                ValidationIssue("rationale", "DEFER must explain missing evidence")
            )
    elif decision == "NEEDS_PLANNER_APPROVAL":
        explanation = " ".join((rationale, *validation_plan)).lower()
        required_terms = ("scope", "allowed", "approval", "permission", "path")
        if not any(term in explanation for term in required_terms):
            issues.append(
                ValidationIssue(
                    "decision",
                    "NEEDS_PLANNER_APPROVAL must explain the missing or expanded scope",
                )
            )
    elif decision == "NEEDS_HUMAN_REVIEW" and not rationale:
        issues.append(
            ValidationIssue(
                "rationale",
                "NEEDS_HUMAN_REVIEW must explain why review is needed",
            )
        )
    return tuple(issues)


def validate_source_patch_todo_semantics(
    *,
    files_to_write: tuple[str, ...],
    entry_points: tuple[str, ...],
    invariants: tuple[str, ...],
    validation_plan: tuple[str, ...],
    rollback_plan: str,
    label: str = FLOW_CANDIDATE_SOURCE_PATCH_TODO,
) -> tuple[ValidationIssue, ...]:
    """Validate required review metadata for source patch proposals."""

    issues: list[ValidationIssue] = []
    if not files_to_write:
        issues.append(
            ValidationIssue(
                "files_to_write",
                f"{label} requires at least one proposed target or artifact path",
            )
        )
    if not entry_points:
        issues.append(
            ValidationIssue(
                "entry_points",
                f"{label} requires non-empty entry_points",
            )
        )
    if not invariants:
        issues.append(
            ValidationIssue(
                "invariants",
                f"{label} requires correctness or compatibility invariants",
            )
        )
    if not validation_plan:
        issues.append(
            ValidationIssue(
                "validation_plan",
                f"{label} requires validation commands or exact gates",
            )
        )
    if not rollback_plan.strip():
        issues.append(
            ValidationIssue(
                "rollback_plan",
                f"{label} requires a rollback plan",
            )
        )
    return tuple(issues)


def validate_source_patch_payload(
    *,
    candidate_kind: str,
    source_patch: Mapping[str, Any] | None,
    context: CycleContext,
    files_to_write: tuple[str, ...],
) -> tuple[ValidationIssue, ...]:
    """Validate the machine-applicable patch payload for source_patch_diff."""

    if candidate_kind != FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
        if source_patch:
            return (
                ValidationIssue(
                    "source_patch",
                    "source_patch payload is only allowed for source_patch_diff",
                ),
            )
        return ()

    if source_patch is None:
        return (
            ValidationIssue(
                "source_patch",
                "source_patch_diff requires a source_patch object",
            ),
        )

    issues: list[ValidationIssue] = []
    patch_format = source_patch.get("patch_format")
    if patch_format != "unified_diff":
        issues.append(
            ValidationIssue(
                "source_patch.patch_format",
                "source_patch_diff requires patch_format='unified_diff'",
            )
        )

    apply_strategy = source_patch.get("apply_strategy")
    if apply_strategy not in (None, "isolated_workspace"):
        issues.append(
            ValidationIssue(
                "source_patch.apply_strategy",
                "source_patch_diff apply_strategy must be 'isolated_workspace'",
            )
        )

    diff_text = source_patch.get("diff")
    if not isinstance(diff_text, str) or not diff_text.strip():
        issues.append(
            ValidationIssue(
                "source_patch.diff",
                "source_patch_diff requires a non-empty unified diff",
            )
        )
        return tuple(issues)

    patch_paths, path_issues = validate_unified_diff_paths(
        diff_text,
        context=context,
    )
    issues.extend(path_issues)
    if patch_paths and files_to_write:
        declared_sources = {path for path in files_to_write if not path.startswith("experiments/")}
        missing = sorted(path for path in patch_paths if path not in declared_sources)
        for path in missing:
            issues.append(
                ValidationIssue(
                    "files_to_write",
                    f"source_patch_diff target is missing from files_to_write: {path}",
                )
            )
    return tuple(issues)


def validate_unified_diff_paths(
    diff_text: str,
    *,
    context: CycleContext,
) -> tuple[tuple[str, ...], tuple[ValidationIssue, ...]]:
    """Extract and validate target paths from a unified diff."""

    paths: list[str] = []
    issues: list[ValidationIssue] = []
    for raw_line in diff_text.splitlines():
        line = raw_line.strip()
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                paths.extend(
                    path
                    for path in (
                        _strip_diff_prefix(parts[2]),
                        _strip_diff_prefix(parts[3]),
                    )
                    if path
                )
        elif line.startswith("--- ") or line.startswith("+++ "):
            value = line[4:].split("\t", 1)[0].strip()
            path = _strip_diff_prefix(value)
            if path:
                paths.append(path)

    unique_paths = tuple(sorted(set(paths)))
    if not unique_paths:
        return (), (
            ValidationIssue(
                "source_patch.diff",
                "unified diff must contain diff --git or ---/+++ file headers",
            ),
        )

    for index, path in enumerate(unique_paths):
        if path == "/dev/null":
            continue
        _, path_issues = validate_path_in_allowed_scope(
            path,
            repo_root=context.repo_root,
            allowed_roots=_candidate_kind_allowed_roots(
                context, FLOW_CANDIDATE_SOURCE_PATCH_DIFF
            ),
            field=f"source_patch.diff.paths[{index}]",
            scope_label="Flow Agent source_patch_diff write scope",
        )
        issues.extend(path_issues)

        if _assignment_allowed_roots(context):
            _, assignment_issues = validate_path_in_allowed_scope(
                path,
                repo_root=context.repo_root,
                allowed_roots=_assignment_allowed_roots(context),
                field=f"source_patch.diff.paths[{index}]",
                scope_label="assignment allowed_to_edit",
            )
            issues.extend(assignment_issues)

    return tuple(path for path in unique_paths if path != "/dev/null"), tuple(issues)


def _strip_diff_prefix(path: str) -> str:
    if path in ("/dev/null", "dev/null"):
        return "/dev/null"
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _benchmark_hard_code_tokens(benchmark_scope: tuple[str, ...]) -> tuple[str, ...]:
    tokens = {"benchmarks/"}
    for benchmark in benchmark_scope:
        lower_path = benchmark.lower()
        tokens.add(lower_path)
        tokens.add(Path(lower_path).name)
        tokens.add(Path(lower_path).stem)
    return tuple(sorted(tokens))


def _failed(issues: tuple[ValidationIssue, ...]) -> ValidationResult:
    return ValidationResult(
        ok=False,
        response=None,
        issues=issues,
        decision="NEEDS_HUMAN_REVIEW",
    )


def _type_issue(field: str, expected: str) -> ValidationIssue:
    return ValidationIssue(field=field, message=f"must be {expected}")
