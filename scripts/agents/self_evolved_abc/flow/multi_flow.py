"""Frozen multi-flow evaluation definitions and deterministic aggregation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import median, median_low
from typing import Mapping, Sequence


FLOW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MULTI_FLOW_SCHEMA_VERSION = 1
# These are the eight standard technology-independent ABC aliases from
# ``abc.rc``.  They are expanded here so the frozen evaluation contract does
# not silently change if a candidate binary ships a different alias file.
# ``render_qor_script`` strashes the input before these recipes run; that setup
# command and its final output strash are not part of the named aliases.
DEFAULT_EVALUATION_FLOWS = (
    {
        "flow_id": "resyn",
        "kind": "commands",
        "commands": (
            "balance",
            "rewrite",
            "rewrite -z",
            "balance",
            "rewrite -z",
            "balance",
        ),
    },
    {
        "flow_id": "resyn2",
        "kind": "commands",
        "commands": (
            "balance",
            "rewrite",
            "refactor",
            "balance",
            "rewrite",
            "rewrite -z",
            "balance",
            "refactor -z",
            "rewrite -z",
            "balance",
        ),
    },
    {
        "flow_id": "resyn2a",
        "kind": "commands",
        "commands": (
            "balance",
            "rewrite",
            "balance",
            "rewrite",
            "rewrite -z",
            "balance",
            "rewrite -z",
            "balance",
        ),
    },
    {
        "flow_id": "resyn3",
        "kind": "commands",
        "commands": (
            "balance",
            "resub",
            "resub -K 6",
            "balance",
            "resub -z",
            "resub -z -K 6",
            "balance",
            "resub -z -K 5",
            "balance",
        ),
    },
    {
        "flow_id": "compress",
        "kind": "commands",
        "commands": (
            "balance -l",
            "rewrite -l",
            "rewrite -z -l",
            "balance -l",
            "rewrite -z -l",
            "balance -l",
        ),
    },
    {
        "flow_id": "compress2",
        "kind": "commands",
        "commands": (
            "balance -l",
            "rewrite -l",
            "refactor -l",
            "balance -l",
            "rewrite -l",
            "rewrite -z -l",
            "balance -l",
            "refactor -z -l",
            "rewrite -z -l",
            "balance -l",
        ),
    },
    {
        "flow_id": "resyn2rs",
        "kind": "commands",
        "commands": (
            "balance",
            "resub -K 6",
            "rewrite",
            "resub -K 6 -N 2",
            "refactor",
            "resub -K 8",
            "balance",
            "resub -K 8 -N 2",
            "rewrite",
            "resub -K 10",
            "rewrite -z",
            "resub -K 10 -N 2",
            "balance",
            "resub -K 12",
            "refactor -z",
            "resub -K 12 -N 2",
            "rewrite -z",
            "balance",
        ),
    },
    {
        "flow_id": "compress2rs",
        "kind": "commands",
        "commands": (
            "balance -l",
            "resub -K 6 -l",
            "rewrite -l",
            "resub -K 6 -N 2 -l",
            "refactor -l",
            "resub -K 8 -l",
            "balance -l",
            "resub -K 8 -N 2 -l",
            "rewrite -l",
            "resub -K 10 -l",
            "rewrite -z -l",
            "resub -K 10 -N 2 -l",
            "balance -l",
            "resub -K 12 -l",
            "refactor -z -l",
            "resub -K 12 -N 2 -l",
            "rewrite -z -l",
            "balance -l",
        ),
    },
)
DEFAULT_FLOW_AGGREGATION = {
    "schema_version": MULTI_FLOW_SCHEMA_VERSION,
    "metric_aggregation": "median",
    "vote_rule": "strict_majority",
    "require_all_flows_cec": True,
    "require_all_flows_nonregressing": True,
}


@dataclass(frozen=True)
class EvaluationFlow:
    flow_id: str
    kind: str
    commands: tuple[str, ...]


def default_evaluation_flows() -> list[dict[str, object]]:
    """Return a JSON-safe copy of the frozen eight-flow ABC portfolio."""

    return [
        {
            "flow_id": str(item["flow_id"]),
            "kind": str(item["kind"]),
            **(
                {"commands": list(item["commands"])}
                if "commands" in item
                else {}
            ),
        }
        for item in DEFAULT_EVALUATION_FLOWS
    ]


def default_flow_aggregation() -> dict[str, object]:
    return dict(DEFAULT_FLOW_AGGREGATION)


def normalize_evaluation_flows(value: object) -> tuple[EvaluationFlow, ...]:
    """Validate the frozen multi-flow payload stored in an assignment."""

    raw = value if value not in (None, "") else default_evaluation_flows()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("evaluation_flows must be a sequence")
    flows: list[EvaluationFlow] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("evaluation_flows entries must be objects")
        flow_id = str(item.get("flow_id", "")).strip()
        if not FLOW_ID_RE.fullmatch(flow_id) or flow_id in seen:
            raise ValueError(f"invalid or duplicate multi-flow id: {flow_id!r}")
        kind = str(item.get("kind", "commands")).strip()
        commands_raw = item.get("commands", ())
        if kind == "candidate_recipe":
            commands: tuple[str, ...] = ()
        elif kind == "commands":
            if not isinstance(commands_raw, Sequence) or isinstance(
                commands_raw, (str, bytes)
            ):
                raise ValueError(f"multi-flow {flow_id!r} commands must be a sequence")
            commands = tuple(
                str(command).strip().rstrip(";")
                for command in commands_raw
                if str(command).strip()
            )
            if not commands:
                raise ValueError(f"multi-flow {flow_id!r} has no commands")
        else:
            raise ValueError(f"unsupported multi-flow kind: {kind!r}")
        seen.add(flow_id)
        flows.append(EvaluationFlow(flow_id=flow_id, kind=kind, commands=commands))
    if not flows:
        raise ValueError("evaluation_flows must contain at least one flow")
    return tuple(flows)


def normalized_evaluation_flows(value: object) -> list[dict[str, object]]:
    """Return canonical JSON-safe flow entries for assignment/contract storage."""

    return [
        {
            "flow_id": flow.flow_id,
            "kind": flow.kind,
            **({"commands": list(flow.commands)} if flow.commands else {}),
        }
        for flow in normalize_evaluation_flows(value)
    ]


def normalize_flow_aggregation(value: object) -> dict[str, object]:
    """Validate policy knobs while preserving a conservative default."""

    payload = default_flow_aggregation()
    if value not in (None, ""):
        if not isinstance(value, Mapping):
            raise ValueError("flow_aggregation must be an object")
        payload.update(dict(value))
    if payload.get("metric_aggregation") != "median":
        raise ValueError("only median multi-flow aggregation is supported")
    if payload.get("vote_rule") != "strict_majority":
        raise ValueError("only strict_majority multi-flow voting is supported")
    for key in ("require_all_flows_cec", "require_all_flows_nonregressing"):
        if not isinstance(payload.get(key), bool):
            raise ValueError(f"multi-flow aggregation {key} must be boolean")
    payload["schema_version"] = MULTI_FLOW_SCHEMA_VERSION
    return payload


def flow_commands(
    flow: EvaluationFlow,
    *,
    candidate_flow_path: str,
) -> tuple[str, ...]:
    """Return ABC commands for one frozen flow definition."""

    if flow.kind == "candidate_recipe":
        return (f"source {candidate_flow_path}",)
    return flow.commands


def aggregate_flow_comparison_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    flow_ids: Sequence[str],
    aggregation: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    """Aggregate detailed per-flow comparisons into one safe row per design.

    The median supplies a robust QoR vector; the vote exposes which flow
    families agree.  Promotion remains conservative by default: all flow CEC
    runs must pass and no flow may regress AND count, even if a strict majority
    votes for the candidate.
    """

    policy = normalize_flow_aggregation(aggregation)
    expected_ids = tuple(str(flow_id) for flow_id in flow_ids)
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        raise ValueError("multi-flow aggregation requires unique flow ids")
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        benchmark = str(row.get("benchmark", "")).strip()
        if not benchmark:
            raise ValueError("multi-flow comparison row lacks benchmark")
        grouped.setdefault(benchmark, []).append(row)

    aggregate_rows: list[dict[str, object]] = []
    vote_rows: list[dict[str, object]] = []
    scoreboard: dict[str, dict[str, int]] = {
        flow_id: {
            "flow_id": flow_id,
            "benchmark_count": 0,
            "cec_pass_count": 0,
            "candidate_win_count": 0,
            "baseline_win_count": 0,
            "tie_count": 0,
            "invalid_count": 0,
            "total_and_delta_candidate_minus_baseline": 0,
        }
        for flow_id in expected_ids
    }
    for benchmark in sorted(grouped):
        group = grouped[benchmark]
        by_flow = {str(row.get("flow_id", "")).strip(): row for row in group}
        missing = [flow_id for flow_id in expected_ids if flow_id not in by_flow]
        unexpected = sorted(set(by_flow) - set(expected_ids))
        duplicate = len(by_flow) != len(group)
        ordered = [by_flow[flow_id] for flow_id in expected_ids if flow_id in by_flow]
        votes = [_flow_vote(row) for row in ordered]
        cec_passes = [str(row.get("cec_status", "")) == "cec_pass" for row in ordered]
        valid_metrics = [_has_complete_metrics(row) for row in ordered]
        for flow_id in expected_ids:
            stat = scoreboard[flow_id]
            row = by_flow.get(flow_id)
            if row is None:
                stat["invalid_count"] += 1
                continue
            stat["benchmark_count"] += 1
            if str(row.get("cec_status", "")) == "cec_pass":
                stat["cec_pass_count"] += 1
            vote = _flow_vote(row)
            if vote == "candidate":
                stat["candidate_win_count"] += 1
            elif vote == "baseline":
                stat["baseline_win_count"] += 1
            elif vote == "tie":
                stat["tie_count"] += 1
            else:
                stat["invalid_count"] += 1
            delta = _as_int(row.get("and_delta_candidate_minus_baseline"))
            if delta is not None:
                stat["total_and_delta_candidate_minus_baseline"] += delta

        candidate_votes = votes.count("candidate")
        baseline_votes = votes.count("baseline")
        tie_votes = votes.count("tie")
        invalid_votes = votes.count("invalid")
        quorum = len(expected_ids) // 2 + 1
        if candidate_votes >= quorum:
            outcome = "candidate_wins"
        elif baseline_votes >= quorum:
            outcome = "baseline_wins"
        elif invalid_votes:
            outcome = "incomplete"
        else:
            outcome = "tie_or_split"
        full_cec = not missing and not unexpected and not duplicate and all(cec_passes)
        all_nonregressing = all(vote != "baseline" for vote in votes) and not invalid_votes
        correctness_backed = full_cec and all(valid_metrics)
        safe_for_promotion = correctness_backed and (
            all_nonregressing or not bool(policy["require_all_flows_nonregressing"])
        )
        representative = ordered[0] if ordered else {}
        aggregate_row = {
            "benchmark": benchmark,
            "flow_id": "aggregate_median",
            "cec_status": "cec_pass" if full_cec else _aggregate_cec_status(ordered),
            "correctness_backed": correctness_backed,
            "baseline_aig_nodes": _median_field(ordered, "baseline_aig_nodes") if correctness_backed else "",
            "candidate_aig_nodes": _median_field(ordered, "candidate_aig_nodes") if correctness_backed else "",
            "and_delta_candidate_minus_baseline": _median_field(ordered, "and_delta_candidate_minus_baseline") if correctness_backed else "",
            "and_improve_pct": _median_float_field(ordered, "and_improve_pct") if correctness_backed else "",
            "baseline_aig_depth": _median_field(ordered, "baseline_aig_depth") if correctness_backed else "",
            "candidate_aig_depth": _median_field(ordered, "candidate_aig_depth") if correctness_backed else "",
            "depth_delta_candidate_minus_baseline": _median_field(ordered, "depth_delta_candidate_minus_baseline") if correctness_backed else "",
            "baseline_runtime_seconds": _median_float_field(ordered, "baseline_runtime_seconds") if correctness_backed else "",
            "candidate_runtime_seconds": _median_float_field(ordered, "candidate_runtime_seconds") if correctness_backed else "",
            "runtime_delta_seconds": _median_float_field(ordered, "runtime_delta_seconds") if correctness_backed else "",
            "flow_count": len(expected_ids),
            "cec_pass_flow_count": sum(cec_passes),
            "candidate_vote_count": candidate_votes,
            "baseline_vote_count": baseline_votes,
            "tie_vote_count": tie_votes,
            "invalid_flow_count": invalid_votes + len(missing) + len(unexpected),
            "flow_vote_outcome": outcome,
            "all_flows_nonregressing": all_nonregressing,
            "safe_for_promotion": safe_for_promotion,
            "frontend_kind": representative.get("frontend_kind", ""),
            "frontend_status": representative.get("frontend_status", ""),
            "skipped_reason": _join_reasons(
                [
                    str(row.get("skipped_reason", ""))
                    for row in ordered
                ]
                + ([f"missing_flow={','.join(missing)}"] if missing else [])
                + ([f"unexpected_flow={','.join(unexpected)}"] if unexpected else [])
                + (["duplicate_flow_rows"] if duplicate else [])
            ),
        }
        aggregate_rows.append(aggregate_row)
        vote_rows.append(
            {
                "benchmark": benchmark,
                "flow_count": len(expected_ids),
                "vote_quorum": quorum,
                "candidate_vote_count": candidate_votes,
                "baseline_vote_count": baseline_votes,
                "tie_vote_count": tie_votes,
                "invalid_flow_count": invalid_votes + len(missing) + len(unexpected),
                "flow_vote_outcome": outcome,
                "all_flows_cec_pass": full_cec,
                "all_flows_nonregressing": all_nonregressing,
                "safe_for_promotion": safe_for_promotion,
            }
        )
    summary = {
        "schema_version": MULTI_FLOW_SCHEMA_VERSION,
        "flow_ids": list(expected_ids),
        "aggregation": dict(policy),
        "benchmark_count": len(aggregate_rows),
        "safe_for_promotion_count": sum(
            bool(row["safe_for_promotion"]) for row in aggregate_rows
        ),
        "candidate_vote_wins": sum(
            row["flow_vote_outcome"] == "candidate_wins" for row in aggregate_rows
        ),
        "baseline_vote_wins": sum(
            row["flow_vote_outcome"] == "baseline_wins" for row in aggregate_rows
        ),
        "scoreboard": [scoreboard[flow_id] for flow_id in expected_ids],
    }
    return aggregate_rows, vote_rows, summary


def _flow_vote(row: Mapping[str, object]) -> str:
    if str(row.get("cec_status", "")) != "cec_pass" or not _has_complete_metrics(row):
        return "invalid"
    and_delta = _as_int(row.get("and_delta_candidate_minus_baseline"))
    depth_delta = _as_int(row.get("depth_delta_candidate_minus_baseline"))
    assert and_delta is not None and depth_delta is not None
    if and_delta < 0 or (and_delta == 0 and depth_delta < 0):
        return "candidate"
    if and_delta > 0 or (and_delta == 0 and depth_delta > 0):
        return "baseline"
    return "tie"


def _has_complete_metrics(row: Mapping[str, object]) -> bool:
    required_ints = (
        "baseline_aig_nodes",
        "candidate_aig_nodes",
        "and_delta_candidate_minus_baseline",
        "baseline_aig_depth",
        "candidate_aig_depth",
        "depth_delta_candidate_minus_baseline",
    )
    return all(_as_int(row.get(key)) is not None for key in required_ints) and all(
        _as_float(row.get(key)) is not None
        for key in (
            "and_improve_pct",
            "baseline_runtime_seconds",
            "candidate_runtime_seconds",
            "runtime_delta_seconds",
        )
    )


def _aggregate_cec_status(rows: Sequence[Mapping[str, object]]) -> str:
    statuses = [str(row.get("cec_status", "")) for row in rows]
    if "cec_fail" in statuses:
        return "cec_fail"
    if "cec_timeout" in statuses:
        return "cec_timeout"
    if "cec_crash" in statuses:
        return "cec_crash"
    if "cec_skipped" in statuses:
        return "cec_skipped"
    return "cec_unparseable"


def _median_field(rows: Sequence[Mapping[str, object]], key: str) -> int | str:
    values = [_as_int(row.get(key)) for row in rows]
    if any(value is None for value in values):
        return ""
    # Keep discrete QoR fields integral even when a caller configures an even
    # number of flows. ``median_low`` is deterministic and always selects an
    # observed value instead of manufacturing a fractional AND/depth count.
    return int(median_low([value for value in values if value is not None]))


def _median_float_field(rows: Sequence[Mapping[str, object]], key: str) -> str:
    values = [_as_float(row.get(key)) for row in rows]
    if any(value is None for value in values):
        return ""
    return f"{median([value for value in values if value is not None]):.6f}"


def _as_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _join_reasons(values: Sequence[str]) -> str:
    return "; ".join(sorted({value.strip() for value in values if value.strip()}))
