"""Deterministic validation for Logic Minimization source-patch replies."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_CANDIDATE_DIAGNOSTIC_ONLY,
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
)
from scripts.agents.self_evolved_abc.flow.validation import (
    flow_response_json_schema,
    validate_flow_agent_response,
)
from scripts.agents.self_evolved_abc.logic.contracts import (
    LOGIC_ABCI_ROOT,
    LOGIC_AGENT_NAME,
    LOGIC_FORBIDDEN_BUILD_FILES,
    LOGIC_PAPER_ROLE,
    LOGIC_REACHABLE_TARGET_COMMANDS,
    LOGIC_SOURCE_PATCH_MODE,
    LOGIC_SOURCE_SUFFIXES,
    LOGIC_SOURCE_TOUCHPOINTS,
)
from scripts.agents.self_evolved_abc.schemas import (
    FlowAgentResponse,
    ValidationIssue,
    ValidationResult,
)


LOGIC_RESPONSE_CANDIDATE_KINDS = (
    FLOW_CANDIDATE_SOURCE_PATCH_DIFF,
    FLOW_CANDIDATE_DIAGNOSTIC_ONLY,
)
LOGIC_MAX_PATCHED_FILES = 3

_COMPILE_TERMS = re.compile(
    r"\b(?:build|compile|compilation|make|candidate binary)\b", re.IGNORECASE
)
_CEC_TERMS = re.compile(
    r"\b(?:cec|dsat|formal equivalence|equivalence check(?:er|ing)?)\b",
    re.IGNORECASE,
)
_QOR_TERMS = re.compile(r"\bqor\b", re.IGNORECASE)
_NODE_TERMS = re.compile(r"\b(?:and|aig|node|edge)s?(?: count)?\b", re.IGNORECASE)
_DEPTH_TERMS = re.compile(r"\b(?:depth|level|lev)\b", re.IGNORECASE)
_CEC_REJECTION_TERMS = re.compile(
    r"\b(?:mismatch|counterexample|reject|terminate|abort|fail)\w*\b",
    re.IGNORECASE,
)
_ALL_DESIGNS_TERMS = re.compile(
    r"\b(?:all|every|full)\b.*\b(?:benchmark|design|scope)s?\b|"
    r"\b(?:benchmark|design|scope)s?\b.*\b(?:all|every|full)\b",
    re.IGNORECASE,
)
_EQUIVALENCE_INVARIANT = re.compile(
    r"\b(?:combinational equivalence|functional equivalence|functional semantics|"
    r"boolean function|same boolean)\b",
    re.IGNORECASE,
)
_COMBINATIONAL_INVARIANT = re.compile(r"\bcombinational\b", re.IGNORECASE)
_NO_RETIMING_INVARIANT = re.compile(
    r"\b(?:no|without|forbid\w*|avoid\w*)\b.{0,32}\bretim\w*\b|"
    r"\b(?:do not|must not)\b.{0,32}\bretim\w*\b",
    re.IGNORECASE,
)
_NO_SEQUENTIAL_INVARIANT = re.compile(
    r"\b(?:no|without|forbid\w*|avoid\w*)\b.{0,40}"
    r"\b(?:sequential|latch|register|initial state)\w*\b|"
    r"\b(?:do not|must not)\b.{0,40}"
    r"\b(?:sequential|latch|register|initial state)\w*\b",
    re.IGNORECASE,
)
_FORBIDDEN_SEQUENTIAL_CHANGE = re.compile(
    r"\b(?:retim\w*|latch\w*|register\w*|initial[_ ]state\w*|"
    r"Abc_NtkRetime\w*|Saig_\w*)\b",
    re.IGNORECASE,
)
_NONDETERMINISTIC_CHANGE = re.compile(
    r"\b(?:rand|random|srand|time|clock|gettimeofday|arc4random|getenv|"
    r"system|popen|fopen)\s*\(|__DATE__|__TIME__",
    re.IGNORECASE,
)
_LOCAL_ABSOLUTE_PATH = re.compile(
    r"(?:/Users/|/home/|/private/tmp/|[A-Za-z]:\\\\)"
)
_DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")


@dataclass(frozen=True)
class LogicDiffSection:
    """One ordinary existing-file section in a model-proposed unified diff."""

    header_old_path: str
    header_new_path: str
    old_path: str
    new_path: str
    body: tuple[str, ...]

    @property
    def target_path(self) -> str:
        return self.new_path


def logic_response_json_schema() -> Mapping[str, Any]:
    """Return the strict JSON schema advertised by the Logic Agent."""

    schema = deepcopy(flow_response_json_schema())
    properties = schema["properties"]
    properties["candidate_kind"]["enum"] = list(LOGIC_RESPONSE_CANDIDATE_KINDS)
    source_patch = properties["source_patch"]
    source_patch["required"] = [
        "patch_format",
        "target_scope",
        "apply_strategy",
        "diff",
    ]
    source_patch["additionalProperties"] = False
    source_patch["properties"]["patch_format"]["enum"] = ["unified_diff"]
    source_patch["properties"]["target_scope"]["enum"] = [LOGIC_ABCI_ROOT]
    source_patch["properties"]["apply_strategy"]["enum"] = [
        "isolated_workspace"
    ]
    return schema


def validate_logic_agent_response(
    data: Mapping[str, Any],
    context: CycleContext,
) -> ValidationResult:
    """Validate role identity, patch scope, semantics, and gate ordering."""

    assignment_issues = validate_logic_assignment_contract(context)
    if assignment_issues:
        return _failed(assignment_issues)

    base = validate_flow_agent_response(data, context)
    if not base.ok or base.response is None:
        return base
    response = base.response

    if response.decision != "PROPOSE_CANDIDATE":
        if response.candidate_kind != FLOW_CANDIDATE_DIAGNOSTIC_ONLY:
            return _failed(
                (
                    ValidationIssue(
                        "candidate_kind",
                        "non-proposal Logic decisions require diagnostic_only",
                    ),
                )
            )
        return base

    issues = list(validate_logic_candidate_contract(response, context))
    if issues:
        return _failed(tuple(issues))
    return base


def validate_logic_assignment_contract(
    context: CycleContext,
) -> tuple[ValidationIssue, ...]:
    """Fail closed unless the assignment is exactly the paper's Logic role."""

    assignment = context.assignment
    issues: list[ValidationIssue] = []
    exact_fields = {
        "agent_name": LOGIC_AGENT_NAME,
        "paper_role": LOGIC_PAPER_ROLE,
        "source_patch_mode": LOGIC_SOURCE_PATCH_MODE,
        "subsystem": LOGIC_ABCI_ROOT,
    }
    for field, expected in exact_fields.items():
        actual = str(assignment.get(field, "")).strip().rstrip("/")
        if actual != expected:
            issues.append(
                ValidationIssue(field, f"Logic assignment requires {expected!r}")
            )

    roots = _normalized_paths(assignment.get("source_patch_allowed_roots", ()))
    if roots != (LOGIC_ABCI_ROOT,):
        issues.append(
            ValidationIssue(
                "source_patch_allowed_roots",
                f"Logic source ownership must be exactly [{LOGIC_ABCI_ROOT!r}]",
            )
        )

    edit_roots = _normalized_paths(assignment.get("allowed_to_edit", ()))
    if LOGIC_ABCI_ROOT not in edit_roots:
        issues.append(
            ValidationIssue(
                "allowed_to_edit",
                "Logic assignment must include its ABCI source root",
            )
        )
    conflicting = tuple(
        root
        for root in edit_roots
        if root.startswith("third_party/FlowTune/") and root != LOGIC_ABCI_ROOT
    )
    if conflicting:
        issues.append(
            ValidationIssue(
                "allowed_to_edit",
                "Logic assignment contains non-ABCI FlowTune source roots: "
                + ", ".join(conflicting),
            )
        )

    target = str(assignment.get("target_command", "")).strip()
    if target not in LOGIC_REACHABLE_TARGET_COMMANDS:
        issues.append(
            ValidationIssue(
                "target_command",
                "Logic target must be one of: "
                + ", ".join(LOGIC_REACHABLE_TARGET_COMMANDS),
            )
        )
    elif not _evaluation_reaches_target(assignment, target):
        issues.append(
            ValidationIssue(
                "evaluation_flow_commands",
                f"evaluation flow does not reach Logic target {target!r}",
            )
        )
    return tuple(issues)


def validate_logic_candidate_contract(
    response: FlowAgentResponse,
    context: CycleContext,
) -> tuple[ValidationIssue, ...]:
    """Validate one proposed Logic source diff beyond generic Flow checks."""

    issues: list[ValidationIssue] = []
    if response.candidate_kind != FLOW_CANDIDATE_SOURCE_PATCH_DIFF:
        return (
            ValidationIssue(
                "candidate_kind",
                "PROPOSE_CANDIDATE requires source_patch_diff for Logic",
            ),
        )

    patch = response.source_patch or {}
    required_patch_values = {
        "patch_format": "unified_diff",
        "target_scope": LOGIC_ABCI_ROOT,
        "apply_strategy": "isolated_workspace",
    }
    for field, expected in required_patch_values.items():
        if patch.get(field) != expected:
            issues.append(
                ValidationIssue(
                    f"source_patch.{field}",
                    f"Logic patch requires {field}={expected!r}",
                )
            )
    extras = sorted(set(patch) - {*required_patch_values, "diff"})
    if extras:
        issues.append(
            ValidationIssue(
                "source_patch", "unexpected Logic patch fields: " + ", ".join(extras)
            )
        )

    diff_text = patch.get("diff")
    if not isinstance(diff_text, str):
        return tuple(
            (*issues, ValidationIssue("source_patch.diff", "must be a string"))
        )
    sections, section_issues = extract_logic_diff_sections(diff_text)
    issues.extend(section_issues)
    if not sections:
        return tuple(issues)

    targets = tuple(section.target_path for section in sections)
    if len(set(targets)) != len(targets):
        issues.append(
            ValidationIssue("source_patch.diff", "duplicate file sections are forbidden")
        )
    if len(set(targets)) > LOGIC_MAX_PATCHED_FILES:
        issues.append(
            ValidationIssue(
                "source_patch.diff",
                f"one Logic hypothesis may touch at most {LOGIC_MAX_PATCHED_FILES} files",
            )
        )

    for index, section in enumerate(sections):
        issues.extend(_validate_diff_section(section, context, index=index))

    declared_sources = tuple(
        path
        for path in response.files_to_write
        if not path.startswith(f"experiments/{context.cycle_id}/agents/")
    )
    if set(declared_sources) != set(targets):
        issues.append(
            ValidationIssue(
                "files_to_write",
                "declared Logic source files must exactly match unified-diff targets",
            )
        )

    issues.extend(_validate_logic_invariants(response.invariants))
    issues.extend(_validate_gate_plan(response.validation_plan))
    issues.extend(_validate_target_reachability(response, targets, context))
    issues.extend(_validate_changed_lines(sections, context))
    return tuple(issues)


def extract_logic_diff_sections(
    diff_text: str,
) -> tuple[tuple[LogicDiffSection, ...], tuple[ValidationIssue, ...]]:
    """Parse ordinary ``diff --git`` sections without applying the patch."""

    sections: list[LogicDiffSection] = []
    issues: list[ValidationIssue] = []
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        field = f"source_patch.diff.sections[{len(sections)}]"
        old_path = str(current.get("old_path", ""))
        new_path = str(current.get("new_path", ""))
        if not old_path or not new_path:
            issues.append(
                ValidationIssue(
                    field,
                    "each diff --git section requires matching --- and +++ headers",
                )
            )
        else:
            sections.append(
                LogicDiffSection(
                    header_old_path=str(current["header_old_path"]),
                    header_new_path=str(current["header_new_path"]),
                    old_path=old_path,
                    new_path=new_path,
                    body=tuple(current["body"]),
                )
            )
        current = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            finish()
            match = _DIFF_HEADER.fullmatch(raw_line)
            if match is None:
                issues.append(
                    ValidationIssue(
                        "source_patch.diff",
                        "diff --git headers must use unquoted repository-relative a/ and b/ paths",
                    )
                )
                current = {
                    "header_old_path": "",
                    "header_new_path": "",
                    "old_path": "",
                    "new_path": "",
                    "body": [],
                    "in_hunk": False,
                }
            else:
                current = {
                    "header_old_path": match.group(1),
                    "header_new_path": match.group(2),
                    "old_path": "",
                    "new_path": "",
                    "body": [],
                    "in_hunk": False,
                }
            continue
        if current is None:
            if raw_line.startswith(("--- ", "+++ ", "@@ ")):
                issues.append(
                    ValidationIssue(
                        "source_patch.diff",
                        "unified diff content must begin with diff --git",
                    )
                )
            continue
        current["body"].append(raw_line)
        if raw_line.startswith("@@ "):
            current["in_hunk"] = True
            continue
        if current["in_hunk"]:
            continue
        if raw_line.startswith("--- "):
            current["old_path"] = _diff_marker_path(raw_line[4:])
        elif raw_line.startswith("+++ "):
            current["new_path"] = _diff_marker_path(raw_line[4:])

    finish()
    if not sections and not issues:
        issues.append(
            ValidationIssue(
                "source_patch.diff", "unified diff requires at least one diff --git section"
            )
        )
    return tuple(sections), tuple(issues)


def _validate_diff_section(
    section: LogicDiffSection,
    context: CycleContext,
    *,
    index: int,
) -> tuple[ValidationIssue, ...]:
    field = f"source_patch.diff.sections[{index}]"
    issues: list[ValidationIssue] = []
    if section.old_path == "/dev/null" or section.new_path == "/dev/null":
        issues.append(
            ValidationIssue(field, "Logic patches may not create or delete source files")
        )
        return tuple(issues)
    if not (
        section.header_old_path
        == section.header_new_path
        == section.old_path
        == section.new_path
    ):
        issues.append(
            ValidationIssue(field, "renames and mismatched diff paths are forbidden")
        )

    target = section.target_path
    target_path = Path(target)
    if target_path.name in LOGIC_FORBIDDEN_BUILD_FILES:
        issues.append(
            ValidationIssue(field, "Logic patches may not edit build metadata")
        )
    if target_path.suffix.lower() not in LOGIC_SOURCE_SUFFIXES:
        issues.append(
            ValidationIssue(field, "Logic patches are limited to existing .c/.h files")
        )
    resolved_root = (context.repo_root / LOGIC_ABCI_ROOT).resolve()
    resolved = (context.repo_root / target_path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        issues.append(
            ValidationIssue(field, f"Logic patch target is outside {LOGIC_ABCI_ROOT}")
        )
    else:
        if not resolved.is_file():
            issues.append(
                ValidationIssue(field, f"Logic patch target must already exist: {target}")
            )

    metadata_markers = (
        "new file mode ",
        "deleted file mode ",
        "rename from ",
        "rename to ",
        "copy from ",
        "copy to ",
        "GIT binary patch",
        "Binary files ",
    )
    if any(
        line.startswith(metadata_markers) or line == "GIT binary patch"
        for line in section.body
    ):
        issues.append(
            ValidationIssue(field, "new/delete/rename/copy/binary diffs are forbidden")
        )
    if not any(line.startswith("@@ ") for line in section.body):
        issues.append(ValidationIssue(field, "unified diff section requires a hunk"))
    changed = tuple(_changed_code_lines(section))
    if not changed:
        issues.append(
            ValidationIssue(field, "Logic patch must contain a non-comment code change")
        )
    return tuple(issues)


def _validate_logic_invariants(
    invariants: Sequence[str],
) -> tuple[ValidationIssue, ...]:
    text = " ".join(invariants)
    checks = (
        (
            _EQUIVALENCE_INVARIANT,
            "invariants",
            "Logic proposal must state functional/combinational equivalence",
        ),
        (
            _COMBINATIONAL_INVARIANT,
            "invariants",
            "Logic proposal must state the combinational boundary",
        ),
        (
            _NO_RETIMING_INVARIANT,
            "invariants",
            "Logic proposal must explicitly forbid retiming",
        ),
        (
            _NO_SEQUENTIAL_INVARIANT,
            "invariants",
            "Logic proposal must explicitly forbid sequential/latch/register changes",
        ),
    )
    return tuple(
        ValidationIssue(field, message)
        for pattern, field, message in checks
        if pattern.search(text) is None
    )


def _validate_gate_plan(
    validation_plan: Sequence[str],
) -> tuple[ValidationIssue, ...]:
    steps = tuple(str(step) for step in validation_plan)
    predicates = (
        ("compile", lambda value: _COMPILE_TERMS.search(value) is not None),
        ("cec", lambda value: _CEC_TERMS.search(value) is not None),
        (
            "qor",
            lambda value: _QOR_TERMS.search(value) is not None
            or (
                _NODE_TERMS.search(value) is not None
                and _DEPTH_TERMS.search(value) is not None
            ),
        ),
    )
    selected: dict[str, tuple[int, str]] = {}
    cursor = -1
    issues: list[ValidationIssue] = []
    for gate, predicate in predicates:
        found = next(
            ((index, step) for index, step in enumerate(steps) if index > cursor and predicate(step)),
            None,
        )
        if found is None:
            issues.append(
                ValidationIssue(
                    "validation_plan",
                    f"Logic validation requires ordered compile -> CEC -> QoR gates; missing {gate}",
                )
            )
            continue
        selected[gate] = found
        cursor = found[0]

    cec_step = selected.get("cec", (-1, ""))[1]
    if cec_step and _ALL_DESIGNS_TERMS.search(cec_step) is None:
        issues.append(
            ValidationIssue(
                "validation_plan",
                "CEC gate must cover every benchmark/design in the evaluation scope",
            )
        )
    combined = " ".join(steps)
    if cec_step and _CEC_REJECTION_TERMS.search(combined) is None:
        issues.append(
            ValidationIssue(
                "validation_plan",
                "CEC mismatch/counterexample must reject or terminate the candidate",
            )
        )
    qor_step = selected.get("qor", (-1, ""))[1]
    if qor_step and not (
        _NODE_TERMS.search(qor_step) and _DEPTH_TERMS.search(qor_step)
    ):
        issues.append(
            ValidationIssue(
                "validation_plan",
                "QoR gate must record AIG/AND node count and depth",
            )
        )
    return tuple(issues)


def _validate_target_reachability(
    response: FlowAgentResponse,
    targets: Sequence[str],
    context: CycleContext,
) -> tuple[ValidationIssue, ...]:
    target_command = str(context.assignment.get("target_command", "")).strip()
    text = " ".join(
        (
            response.rationale,
            response.source_design,
            *response.candidate_steps,
            *response.entry_points,
        )
    ).lower()
    token = "orchestrat" if target_command == "orchestrate" else target_command
    issues: list[ValidationIssue] = []
    if token not in text:
        issues.append(
            ValidationIssue(
                "entry_points",
                f"proposal does not trace the planned Logic target {target_command!r}",
            )
        )
    expected_paths = set(LOGIC_SOURCE_TOUCHPOINTS[target_command])
    if not expected_paths.intersection(targets):
        issues.append(
            ValidationIssue(
                "source_patch.diff",
                f"patch does not touch a reached {target_command!r} ABCI entry point",
            )
        )
    return tuple(issues)


def _validate_changed_lines(
    sections: Sequence[LogicDiffSection],
    context: CycleContext,
) -> tuple[ValidationIssue, ...]:
    changed = "\n".join(
        line for section in sections for line in _all_changed_lines(section)
    )
    issues: list[ValidationIssue] = []
    if _FORBIDDEN_SEQUENTIAL_CHANGE.search(changed):
        issues.append(
            ValidationIssue(
                "source_patch.diff",
                "Logic diff references retiming, latches, registers, or sequential state",
            )
        )
    if _NONDETERMINISTIC_CHANGE.search(changed):
        issues.append(
            ValidationIssue(
                "source_patch.diff",
                "Logic diff introduces nondeterministic or environment/file side effects",
            )
        )
    if _LOCAL_ABSOLUTE_PATH.search(changed):
        issues.append(
            ValidationIssue(
                "source_patch.diff", "Logic diff contains a machine-local absolute path"
            )
        )
    lower = changed.lower()
    for token in _benchmark_tokens(context.benchmark_scope):
        if _contains_benchmark_token(lower, token):
            issues.append(
                ValidationIssue(
                    "source_patch.diff",
                    f"Logic diff hard-codes benchmark/design token {token!r}",
                )
            )
            break
    return tuple(issues)


def _evaluation_reaches_target(
    assignment: Mapping[str, Any],
    target: str,
) -> bool:
    commands = tuple(
        str(item).strip().lower().split(maxsplit=1)[0]
        for item in assignment.get("evaluation_flow_commands", ())
        if str(item).strip()
    )
    if target == "orchestrate":
        return {"rewrite", "resub", "refactor"}.issubset(commands)
    return target in commands


def _diff_marker_path(value: str) -> str:
    path = value.split("\t", 1)[0].strip()
    if path in ("/dev/null", "dev/null"):
        return "/dev/null"
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _all_changed_lines(section: LogicDiffSection) -> tuple[str, ...]:
    in_hunk = False
    lines: list[str] = []
    for line in section.body:
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk or not line.startswith(("+", "-")):
            continue
        if line.startswith(("+++", "---")):
            continue
        lines.append(line[1:])
    return tuple(lines)


def _changed_code_lines(section: LogicDiffSection) -> tuple[str, ...]:
    result: list[str] = []
    for line in _all_changed_lines(section):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*", "*/")):
            continue
        result.append(stripped)
    return tuple(result)


def _benchmark_tokens(scope: Sequence[str]) -> tuple[str, ...]:
    tokens = {"benchmarks/"}
    for item in scope:
        lower = str(item).lower()
        tokens.update((lower, Path(lower).name, Path(lower).stem))
    return tuple(sorted(token for token in tokens if token))


def _contains_benchmark_token(text: str, token: str) -> bool:
    if token == "benchmarks/" or "/" in token:
        return token in text
    return re.search(
        rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", text
    ) is not None


def _normalized_paths(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Sequence[object] = (value,)
    elif isinstance(value, Sequence):
        values = value
    else:
        return ()
    result: list[str] = []
    for item in values:
        path = str(item).strip().rstrip("/")
        if path and path not in result:
            result.append(path)
    return tuple(result)


def _failed(issues: tuple[ValidationIssue, ...]) -> ValidationResult:
    return ValidationResult(
        ok=False,
        response=None,
        issues=issues,
        decision="NEEDS_HUMAN_REVIEW",
    )
