"""Execute planner-requested sensitivity batches and integrate their evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.agents.self_evolved_abc.flow.assignment import (
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_SOURCE_TOUCHPOINTS,
    IMPL_CANDIDATE_LABEL,
)


def run_and_integrate_planner_batch(
    *,
    repo_root: Path,
    assignment_path: Path,
    build_candidate_binary: bool,
    build_jobs: int,
    build_timeout_seconds: float,
    timeout_seconds: float,
) -> str | None:
    """Run a model-free sensitivity batch and update the pending assignment.

    Returns the promoted probe cycle id, an empty string when the batch only
    produced sensitivity evidence, or ``None`` when the batch could not run.
    """

    if not build_candidate_binary:
        print(
            "cycle_loop: automatic planner batch requires "
            "--build-candidate-binary"
        )
        return None

    payload = read_json_object(assignment_path)
    if payload is None:
        return None
    cycle_id = str(payload.get("cycle_id", assignment_path.parent.parent.parent.name))
    meta = payload.get("_planning_meta")
    target_command = (
        str(meta.get("target_command", "")).strip()
        if isinstance(meta, dict)
        else str(payload.get("target_command", "")).strip()
    )
    batch_id = f"{cycle_id}_planner_flow_wide"
    batch_dir = repo_root / "experiments" / "batches" / batch_id
    manifest_path = batch_dir / "manifest.json"
    winner_path = batch_dir / "winner.json"

    if not winner_path.is_file():
        command: list[str] = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.batch_search",
            "--repo-root",
            str(repo_root),
        ]
        if manifest_path.is_file():
            command.extend(("--manifest", str(manifest_path.relative_to(repo_root))))
        else:
            command.extend(
                (
                    "--base-assignment",
                    str(assignment_path.relative_to(repo_root)),
                    "--start-cycle",
                    next_probe_cycle_id(repo_root),
                    "--batch-id",
                    batch_id,
                    "--variant-set",
                    "flow_wide",
                )
            )
            if target_command:
                command.extend(("--target-command", target_command))
        command.extend(
            (
                "--run",
                "--build-candidate-binary",
                "--build-jobs",
                str(max(1, build_jobs)),
                "--build-timeout-seconds",
                f"{build_timeout_seconds:g}",
                "--timeout-seconds",
                f"{timeout_seconds:g}",
                "--cec-timeout-seconds",
                f"{timeout_seconds:g}",
            )
        )
        print(f"cycle_loop: running planner batch {batch_id}")
        completed = subprocess.run(command, cwd=repo_root, check=False)
        if completed.returncode != 0:
            print(
                "cycle_loop: planner batch command failed with exit code "
                f"{completed.returncode}"
            )
            return None

    winner_payload = read_json_object(winner_path)
    if winner_payload is None:
        print(f"cycle_loop: planner batch winner is missing: {winner_path}")
        return None
    return integrate_batch_winner(
        assignment_path=assignment_path,
        batch_id=batch_id,
        winner_payload=winner_payload,
    )


def integrate_batch_winner(
    *,
    assignment_path: Path,
    batch_id: str,
    winner_payload: dict[str, Any],
) -> str | None:
    """Write a batch winner and its sensitivity evidence into an assignment."""

    assignment = read_json_object(assignment_path)
    winner = winner_payload.get("winner")
    if assignment is None or not isinstance(winner, dict):
        return None

    winner_cycle = str(winner.get("cycle_id", "")).strip()
    variant_id = str(winner.get("variant_id", "unknown")).strip()
    if not winner_cycle:
        return None

    promoted = bool(winner_payload.get("promotion_found", False)) or str(
        winner.get("promotion_allowed", "")
    ).lower() == "true"
    command = batch_variant_command(variant_id)
    source_dirs = FLOW_SOURCE_TOUCHPOINTS.get(command, ())
    target_source_dir = str(source_dirs[0]) if source_dirs else str(
        assignment.get("target_source_dir", "")
    )
    summary_rel = f"experiments/batches/{batch_id}/summary.csv"
    winner_rel = f"experiments/batches/{batch_id}/winner.json"
    winner_qor_rel = (
        f"experiments/{winner_cycle}/impl_compare/comparison/qor_delta.csv"
    )
    evidence_paths = (summary_rel, winner_rel, winner_qor_rel)

    for key in ("allowed_to_read", "recent_evidence"):
        current = [str(item) for item in assignment.get(key, ())]
        for path in evidence_paths:
            if path not in current:
                current.append(path)
        assignment[key] = current

    assignment["batch_search_evidence"] = {
        "batch_id": batch_id,
        "promotion_found": promoted,
        "winner_cycle_id": winner_cycle,
        "variant_id": variant_id,
        "decision": winner.get("decision", "missing"),
        "average_and_improve_pct": winner.get("average_and_improve_pct"),
        "total_and_delta_candidate_minus_baseline": winner.get(
            "total_and_delta_candidate_minus_baseline"
        ),
        "improved_benchmark_count": winner.get("improved_benchmark_count"),
        "regressed_benchmark_count": winner.get("regressed_benchmark_count"),
        "summary_path": summary_rel,
        "winner_path": winner_rel,
    }
    evolved_rules = [
        str(item).strip()
        for item in assignment.get("evolved_rules", ())
        if str(item).strip()
    ]
    batch_rule = (
        f"Batch {batch_id} measured `{variant_id}` as its best sensitivity "
        "probe. Do not repeat a swept constant; use the batch QoR vector to "
        "justify a reached decision or scoring change."
    )
    if batch_rule not in evolved_rules:
        evolved_rules.append(batch_rule)
    assignment["evolved_rules"] = evolved_rules[-12:]

    meta = assignment.get("_planning_meta")
    planning_meta = dict(meta) if isinstance(meta, dict) else {}
    planning_meta.update(
        {
            "task_type": "optimization",
            "target_command": command,
            "target_source_dir": target_source_dir,
            "should_skip_llm": False,
            "strategy_rationale": (
                f"Deterministic batch {batch_id} completed; use variant "
                f"{variant_id} as measured sensitivity evidence."
            ),
        }
    )
    assignment["_planning_meta"] = planning_meta
    assignment["planner_should_skip_llm"] = False
    assignment["target_command"] = command
    assignment["target_source_dir"] = target_source_dir
    assignment["planner_hypothesis"] = (
        f"Deterministic batch search `{batch_id}` completed before this model "
        f"call. Best variant `{variant_id}`: decision={winner.get('decision')}, "
        "average AND improvement="
        f"{winner.get('average_and_improve_pct')}, total AND delta="
        f"{winner.get('total_and_delta_candidate_minus_baseline')}, "
        f"improved/regressed={winner.get('improved_benchmark_count')}/"
        f"{winner.get('regressed_benchmark_count')}. Read `{summary_rel}` and "
        f"`{winner_qor_rel}`. Use these measurements to propose a reached "
        "decision/scoring heuristic with a larger effect. Do not repeat a "
        "swept constant or enlarge an unproven capacity limit."
    )

    if promoted:
        workspace = (
            f"experiments/{winner_cycle}/impl_compare/"
            f"{IMPL_CANDIDATE_LABEL}/workspace"
        )
        source_root = f"{workspace}/third_party/FlowTune/src"
        abc_bin = f"{source_root}/abc"
        assignment.update(
            {
                "baseline_kind": "champion",
                "champion_cycle_id": winner_cycle,
                "champion_candidate_id": "candidate_001",
                "champion_source_root": source_root,
                "base_source_root": source_root,
                "champion_abc_bin": abc_bin,
                "baseline_abc_bin": abc_bin,
            }
        )

    normalized = normalize_flow_assignment_scope(assignment)
    assignment_path.write_text(
        json.dumps(normalized, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return winner_cycle if promoted else ""


def batch_variant_command(variant_id: str) -> str:
    if variant_id.startswith("csweep"):
        return "csweep"
    if variant_id.startswith("fx"):
        return "fx"
    for command in FLOW_SOURCE_TOUCHPOINTS:
        if variant_id.startswith(command):
            return command
    return "csweep"


def next_probe_cycle_id(repo_root: Path) -> str:
    highest = 0
    for path in (repo_root / "experiments").glob("probe_*"):
        suffix = path.name[len("probe_") :]
        if path.is_dir() and suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"probe_{highest + 1:03d}"


def read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None
