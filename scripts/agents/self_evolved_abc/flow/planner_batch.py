"""Execute planner-requested sensitivity batches and integrate their evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.agents.self_evolved_abc.cycle_context import CycleContext
from scripts.agents.self_evolved_abc.flow.assignment import (
    normalize_flow_assignment_scope,
)
from scripts.agents.self_evolved_abc.flow.batch_search import (
    BATCH_LINEAGE_SCHEMA_VERSION,
    PatchVariant,
    build_batch_lineage,
    build_variants,
    collect_batch_rows,
    describe_variant_space,
    hash_batch_lineage,
    parse_whole_number,
    summarize_batch_outcomes,
    validate_manifest_base_assignment,
    validate_manifest_lineage,
    validate_batch_measurements,
)
from scripts.agents.self_evolved_abc.flow.contracts import (
    FLOW_SOURCE_TOUCHPOINTS,
    IMPL_CANDIDATE_LABEL,
)
from scripts.agents.self_evolved_abc.flow.paths import impl_compare_root
from scripts.agents.self_evolved_abc.workflow.artifacts import (
    validate_portfolio_cycle_id,
)


PLANNER_BATCH_MAX_PROBES = 12


def run_and_integrate_planner_batch(
    *,
    repo_root: Path,
    assignment_path: Path,
    build_candidate_binary: bool,
    build_jobs: int,
    build_timeout_seconds: float,
    timeout_seconds: float,
    update_baseline: bool = True,
    lineage_context: Mapping[str, object] | None = None,
) -> str | None:
    """Run a model-free sensitivity batch and update the pending assignment.

    Returns the promoted probe cycle id, an empty string when the batch
    produced sensitivity or durable negative evidence, or ``None`` when the
    batch was incomplete, malformed, stale, or could not run.
    """

    repo_root = repo_root.resolve()
    assignment_path = assignment_path.resolve()
    try:
        assignment_path.relative_to(repo_root)
    except ValueError:
        print("cycle_loop: planner batch assignment escapes the repository")
        return None

    if not build_candidate_binary:
        print(
            "cycle_loop: automatic planner batch requires "
            "--build-candidate-binary"
        )
        return None

    payload = read_json_object(assignment_path)
    if payload is None:
        return None
    path_cycle_id = assignment_path.parent.parent.parent.name
    try:
        cycle_id = validate_portfolio_cycle_id(
            str(payload.get("cycle_id", path_cycle_id))
        )
    except ValueError as exc:
        print(f"cycle_loop: invalid planner batch cycle identity: {exc}")
        return None
    if cycle_id != path_cycle_id:
        print("cycle_loop: planner batch assignment cycle does not match its path")
        return None
    meta = payload.get("_planning_meta")
    requested_target = (
        str(meta.get("target_command", "")).strip()
        if isinstance(meta, dict)
        else str(payload.get("target_command", "")).strip()
    )
    try:
        payload, target_command = bind_planner_batch_lineage_context(
            assignment_path=assignment_path,
            assignment=payload,
            target_command=requested_target,
            lineage_context=lineage_context,
        )
        batch_context = CycleContext(repo_root, payload)
        full_variants = build_variants(
            batch_context,
            "flow_wide",
            target_command=target_command,
        )
        variants = select_staged_planner_variants(
            full_variants,
            cycle_id=cycle_id,
            limit=PLANNER_BATCH_MAX_PROBES,
            enabled=not bool(target_command),
        )
        if not variants:
            raise ValueError("planner batch has no variants for the bound source")
        selected_variant_ids = {variant.variant_id for variant in variants}
        include_variants = (
            selected_variant_ids if len(variants) != len(full_variants) else set()
        )
        lineage = build_batch_lineage(
            payload,
            variant_set="flow_wide",
            target_command=target_command,
            include_variants=include_variants,
            variant_space=describe_variant_space(batch_context, variants),
        )
        lineage_hash = hash_batch_lineage(lineage)
    except (OSError, TypeError, ValueError) as exc:
        print(f"cycle_loop: could not bind planner batch lineage: {exc}")
        return None
    batch_id = f"{cycle_id}_planner_flow_wide_{lineage_hash[:12]}"
    batch_dir = repo_root / "experiments" / "batches" / batch_id
    manifest_path = batch_dir / "manifest.json"
    winner_path = batch_dir / "winner.json"

    manifest: dict[str, Any] | None = None
    if manifest_path.is_file():
        manifest = read_json_object(manifest_path)
        try:
            if manifest is None:
                raise ValueError("planner batch manifest is invalid JSON")
            validate_manifest_lineage(
                manifest,
                expected_lineage_hash=lineage_hash,
            )
            validate_manifest_base_assignment(repo_root, manifest)
            validate_planner_batch_manifest_identity(
                repo_root=repo_root,
                manifest=manifest,
                batch_id=batch_id,
                manifest_path=manifest_path,
            )
        except (OSError, ValueError) as exc:
            print(f"cycle_loop: refusing stale planner batch manifest: {exc}")
            return None

    if not winner_path.is_file():
        command: list[str] = [
            sys.executable,
            "-B",
            "-m",
            "scripts.agents.self_evolved_abc.flow.batch_search",
            "--repo-root",
            str(repo_root),
        ]
        if manifest is not None:
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
                    "--expected-lineage-hash",
                    lineage_hash,
                )
            )
            if target_command:
                command.extend(("--target-command", target_command))
            if include_variants:
                command.extend(
                    ("--include-variants", ",".join(sorted(include_variants)))
                )
        if manifest is not None:
            command.extend(("--expected-lineage-hash", lineage_hash))
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
        print(
            f"cycle_loop: running planner batch {batch_id} "
            f"probes={len(variants)}/{len(full_variants)}"
        )
        completed = subprocess.run(command, cwd=repo_root, check=False)
        if completed.returncode != 0:
            print(
                "cycle_loop: planner batch command failed with exit code "
                f"{completed.returncode}"
            )
            return None

    manifest = read_json_object(manifest_path)
    winner_payload = read_json_object(winner_path)
    try:
        if manifest is None:
            raise ValueError("planner batch winner has no manifest")
        validate_manifest_lineage(
            manifest,
            expected_lineage_hash=lineage_hash,
        )
        validate_manifest_base_assignment(repo_root, manifest)
        validate_planner_batch_manifest_identity(
            repo_root=repo_root,
            manifest=manifest,
            batch_id=batch_id,
            manifest_path=manifest_path,
        )
        validate_batch_winner(
            manifest=manifest,
            winner_payload=winner_payload,
            expected_lineage_hash=lineage_hash,
        )
        if winner_payload is None:
            raise ValueError("planner batch winner disappeared during validation")
        validate_batch_measurements(
            repo_root=repo_root,
            manifest=manifest,
            winner_payload=winner_payload,
        )
    except (OSError, ValueError) as exc:
        print(f"cycle_loop: refusing stale planner batch winner: {exc}")
        return None
    if winner_payload is None:
        print(f"cycle_loop: planner batch winner is missing: {winner_path}")
        return None
    winner = winner_payload.get("winner")
    enriched_winner_payload = dict(winner_payload)
    rows = collect_batch_rows(
        repo_root=repo_root,
        manifest=manifest,
        require_reviews=True,
    )
    outcome_summary = summarize_batch_outcomes(rows)
    enriched_winner_payload.update(
        {
            "manifest_path": manifest_path.relative_to(repo_root).as_posix(),
            "outcome_summary": outcome_summary,
        }
    )
    outcome_evidence_path = write_batch_outcome_evidence(
        repo_root=repo_root,
        manifest=manifest,
        lineage_hash=lineage_hash,
        outcome_summary=outcome_summary,
    )
    enriched_winner_payload["outcome_evidence_path"] = (
        outcome_evidence_path.relative_to(repo_root).as_posix()
    )
    if isinstance(winner, dict):
        if str(winner.get("decision", "")).strip() in ("", "missing"):
            print(
                "cycle_loop: planner batch winner has no reviewed decision: "
                f"{winner_path}"
            )
            return None
        winner_item = _winner_manifest_item(manifest, winner)
        winner_patch_rel = str(winner_item.get("patch_path", "")).strip()
        winner_assignment_rel = str(
            winner_item.get("assignment_path", "")
        ).strip()
        winner_patch = repo_root / winner_patch_rel
        enriched_winner_payload.update(
            {
                "winner_patch_path": winner_patch_rel,
                "winner_patch_sha256": hashlib.sha256(
                    winner_patch.read_bytes()
                ).hexdigest(),
                "winner_assignment_path": winner_assignment_rel,
            }
        )
    else:
        diagnostic = outcome_summary.get("diagnostic_probe")
        if isinstance(diagnostic, Mapping):
            diagnostic_item = _winner_manifest_item(manifest, diagnostic)
            diagnostic_assignment_rel = str(
                diagnostic_item.get("assignment_path", "")
            ).strip()
            diagnostic_patch_rel = str(
                diagnostic_item.get("patch_path", "")
            ).strip()
            diagnostic_context = CycleContext.from_assignment_file(
                repo_root, repo_root / diagnostic_assignment_rel
            )
            diagnostic_patch = repo_root / diagnostic_patch_rel
            enriched_winner_payload.update(
                {
                    "diagnostic_assignment_path": diagnostic_assignment_rel,
                    "diagnostic_patch_path": diagnostic_patch_rel,
                    "diagnostic_patch_sha256": hashlib.sha256(
                        diagnostic_patch.read_bytes()
                    ).hexdigest(),
                    "diagnostic_review_path": (
                        impl_compare_root(diagnostic_context)
                        / "comparison"
                        / "review_decision.json"
                    ).relative_to(repo_root).as_posix(),
                    "diagnostic_cec_path": (
                        impl_compare_root(diagnostic_context)
                        / "comparison"
                        / "cec_summary.csv"
                    ).relative_to(repo_root).as_posix(),
                }
            )
        print(
            "cycle_loop: planner batch completed with no correctness-backed "
            "eligible probe; integrating hard-gate failures as negative "
            "Planning evidence "
            f"probes={outcome_summary.get('probe_count', 0)} "
            f"full_cec={outcome_summary.get('full_cec_probe_count', 0)} "
            f"cec_rejected={outcome_summary.get('cec_rejected_probe_count', 0)}"
        )
    return integrate_batch_winner(
        assignment_path=assignment_path,
        batch_id=batch_id,
        winner_payload=enriched_winner_payload,
        update_baseline=update_baseline,
        lineage_hash=lineage_hash,
    )


def write_batch_outcome_evidence(
    *,
    repo_root: Path,
    manifest: Mapping[str, object],
    lineage_hash: str,
    outcome_summary: Mapping[str, object],
) -> Path:
    """Persist bounded CEC diagnostics for post-batch Planning context."""

    manifest_path = repo_root / str(manifest.get("manifest_path", ""))
    batch_dir = manifest_path.parent
    cec_status_counts: dict[str, int] = {}
    cec_exit_code_counts: dict[str, int] = {}
    failed_benchmarks: list[dict[str, object]] = []
    probe_reviews: list[dict[str, object]] = []
    for item in manifest.get("items", ()):
        if not isinstance(item, Mapping):
            continue
        assignment_relative = str(item.get("assignment_path", "")).strip()
        if not assignment_relative:
            continue
        context = CycleContext.from_assignment_file(
            repo_root, repo_root / assignment_relative
        )
        comparison = impl_compare_root(context) / "comparison"
        review_path = comparison / "review_decision.json"
        cec_path = comparison / "cec_summary.csv"
        review = read_json_object(review_path) or {}
        probe_reviews.append(
            {
                "cycle_id": str(item.get("cycle_id", "")),
                "variant_id": str(item.get("variant_id", "")),
                "decision": str(review.get("decision", "missing")),
                "reason": str(review.get("reason", ""))[:1000],
                "next_action": str(review.get("next_action", ""))[:1000],
                "review_path": review_path.relative_to(repo_root).as_posix(),
                "cec_summary_path": cec_path.relative_to(repo_root).as_posix(),
            }
        )
        try:
            with cec_path.open("r", encoding="utf-8", newline="") as stream:
                for row in csv.DictReader(stream):
                    status = str(row.get("cec_status", "")).strip() or "missing"
                    exit_code = str(row.get("cec_exit_code", "")).strip() or "none"
                    cec_status_counts[status] = cec_status_counts.get(status, 0) + 1
                    cec_exit_code_counts[exit_code] = (
                        cec_exit_code_counts.get(exit_code, 0) + 1
                    )
                    if status != "cec_pass" and len(failed_benchmarks) < 24:
                        failed_benchmarks.append(
                            {
                                "cycle_id": str(item.get("cycle_id", "")),
                                "variant_id": str(item.get("variant_id", "")),
                                "benchmark": str(row.get("benchmark", "")),
                                "cec_status": status,
                                "cec_exit_code": exit_code,
                                "skipped_reason": str(
                                    row.get("skipped_reason", "")
                                )[:500],
                                "log_path": str(row.get("log_path", "")),
                            }
                        )
        except (OSError, csv.Error):
            # The canonical review/summary validation remains authoritative.
            # Missing optional detail is recorded without manufacturing data.
            cec_status_counts["diagnostic_unavailable"] = (
                cec_status_counts.get("diagnostic_unavailable", 0) + 1
            )

    payload: dict[str, object] = {
        "schema_version": 1,
        "batch_id": str(manifest.get("batch_id", "")),
        "lineage_hash": lineage_hash,
        "outcome_summary": dict(outcome_summary),
        "cec_status_counts": {
            key: cec_status_counts[key] for key in sorted(cec_status_counts)
        },
        "cec_exit_code_counts": {
            key: cec_exit_code_counts[key]
            for key in sorted(cec_exit_code_counts)
        },
        "failed_benchmarks_sample": failed_benchmarks,
        "probe_reviews": probe_reviews,
        "policy": (
            "Only full-build, exact-scope CEC-backed rows may enter the QoR "
            "winner/frontier. This file is negative diagnostic context and "
            "cannot update the baseline or request exact replay."
        ),
    }
    output = batch_dir / "outcome.json"
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


def integrate_batch_winner(
    *,
    assignment_path: Path,
    batch_id: str,
    winner_payload: dict[str, Any],
    update_baseline: bool = True,
    lineage_hash: str = "",
) -> str | None:
    """Write a reviewed positive or canonical negative batch outcome."""

    assignment = read_json_object(assignment_path)
    raw_winner = winner_payload.get("winner")
    outcome_summary = winner_payload.get("outcome_summary")
    if assignment is None or (
        raw_winner is not None and not isinstance(raw_winner, dict)
    ):
        return None
    has_winner = isinstance(raw_winner, dict)
    winner = raw_winner if has_winner else {}
    if not has_winner and (
        bool(winner_payload.get("promotion_found"))
        or not isinstance(outcome_summary, Mapping)
        or str(outcome_summary.get("status", "")) != "no_eligible_probe"
        or (parse_whole_number(outcome_summary.get("probe_count")) or 0) < 1
        or outcome_summary.get("reviewed_probe_count")
        != outcome_summary.get("probe_count")
        or parse_whole_number(
            outcome_summary.get("evidence_eligible_probe_count")
        )
        != 0
        or parse_whole_number(outcome_summary.get("promotion_probe_count")) != 0
    ):
        return None

    winner_cycle = str(winner.get("cycle_id", "")).strip()
    variant_id = str(winner.get("variant_id", "")).strip()
    if has_winner and (not winner_cycle or not variant_id):
        return None
    diagnostic = (
        outcome_summary.get("diagnostic_probe")
        if isinstance(outcome_summary, Mapping)
        else None
    )
    anchor = winner if has_winner else (
        diagnostic if isinstance(diagnostic, Mapping) else {}
    )
    anchor_variant = str(anchor.get("variant_id", "")).strip()
    command = batch_variant_command(anchor_variant)
    current_meta = assignment.get("_planning_meta")
    if not command and isinstance(current_meta, Mapping):
        command = str(current_meta.get("target_command", "")).strip()
    promoted = bool(
        has_winner
        and (
            bool(winner_payload.get("promotion_found", False))
            or str(winner.get("promotion_allowed", "")).lower() == "true"
        )
    )
    source_dirs = FLOW_SOURCE_TOUCHPOINTS.get(command, ())
    target_source_dir = str(source_dirs[0]) if source_dirs else str(
        assignment.get("target_source_dir", "")
    )
    summary_rel = f"experiments/batches/{batch_id}/summary.csv"
    winner_rel = f"experiments/batches/{batch_id}/winner.json"
    outcome_evidence_rel = str(
        winner_payload.get("outcome_evidence_path", "")
    ).strip()
    if not has_winner and not outcome_evidence_rel:
        return None
    winner_qor_rel = (
        winner_qor_path(assignment_path, winner_cycle) if has_winner else ""
    )
    manifest_rel = str(
        winner_payload.get(
            "manifest_path", f"experiments/batches/{batch_id}/manifest.json"
        )
    ).strip()
    winner_patch_rel = str(winner_payload.get("winner_patch_path", "")).strip()
    winner_patch_sha256 = str(
        winner_payload.get("winner_patch_sha256", "")
    ).strip()
    winner_assignment_rel = str(
        winner_payload.get("winner_assignment_path", "")
    ).strip()
    diagnostic_assignment_rel = str(
        winner_payload.get("diagnostic_assignment_path", "")
    ).strip()
    diagnostic_patch_rel = str(
        winner_payload.get("diagnostic_patch_path", "")
    ).strip()
    diagnostic_patch_sha256 = str(
        winner_payload.get("diagnostic_patch_sha256", "")
    ).strip()
    diagnostic_review_rel = str(
        winner_payload.get("diagnostic_review_path", "")
    ).strip()
    diagnostic_cec_rel = str(
        winner_payload.get("diagnostic_cec_path", "")
    ).strip()
    exact_replay_required = bool(
        promoted
        and not update_baseline
        and winner_patch_rel
        and winner_patch_sha256
    )
    allowed_paths = tuple(
        path
        for path in (
            summary_rel,
            winner_rel,
            outcome_evidence_rel,
            manifest_rel,
            winner_qor_rel,
            winner_assignment_rel,
            winner_patch_rel,
            diagnostic_assignment_rel,
            diagnostic_patch_rel,
            diagnostic_review_rel,
            diagnostic_cec_rel,
        )
        if path
    )
    # Failed-probe QoR is never placed in the automatic QoR evidence channel.
    # Planning receives a bounded negative report plus the representative CEC
    # artifacts and patch, all explicitly marked diagnostic-only.
    recent_paths = allowed_paths if has_winner else tuple(
        path
        for path in (
            outcome_evidence_rel,
            winner_rel,
            manifest_rel,
            diagnostic_review_rel,
            diagnostic_cec_rel,
            diagnostic_patch_rel,
        )
        if path
    )

    for key, paths in (
        ("allowed_to_read", allowed_paths),
        ("recent_evidence", recent_paths),
    ):
        current = [str(item) for item in assignment.get(key, ())]
        for path in paths:
            if path not in current:
                current.append(path)
        assignment[key] = current

    assignment["batch_search_evidence"] = {
        "status": (
            str(outcome_summary.get("status", "winner_selected"))
            if isinstance(outcome_summary, Mapping)
            else "winner_selected"
        ),
        "batch_id": batch_id,
        "lineage_hash": lineage_hash,
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
        "structural_proxy_reward_pct": winner.get(
            "structural_proxy_reward_pct"
        ),
        "total_depth_delta_candidate_minus_baseline": winner.get(
            "total_depth_delta_candidate_minus_baseline"
        ),
        "diverse_frontier": winner_payload.get("diverse_frontier", []),
        "summary_path": summary_rel,
        "winner_path": winner_rel,
        "manifest_path": manifest_rel,
        "winner_patch_path": winner_patch_rel,
        "winner_patch_sha256": winner_patch_sha256,
        "winner_assignment_path": winner_assignment_rel,
        "exact_replay_required": exact_replay_required,
        "outcome_summary": (
            dict(outcome_summary)
            if isinstance(outcome_summary, Mapping)
            else {}
        ),
        "outcome_evidence_path": outcome_evidence_rel,
        "diagnostic_only": not has_winner,
        "diagnostic_assignment_path": diagnostic_assignment_rel,
        "diagnostic_patch_path": diagnostic_patch_rel,
        "diagnostic_patch_sha256": diagnostic_patch_sha256,
        "diagnostic_review_path": diagnostic_review_rel,
        "diagnostic_cec_path": diagnostic_cec_rel,
        "requires_replanning": not update_baseline,
        "planning_consumed": update_baseline,
    }
    evolved_rules = [
        str(item).strip()
        for item in assignment.get("evolved_rules", ())
        if str(item).strip()
    ]
    if not has_winner:
        batch_rule = (
            f"Batch {batch_id} completed with zero correctness-backed eligible "
            "probes. Treat its failed eligibility gates as negative diagnostics "
            "only: "
            "do not rank failed-probe QoR, update the baseline, or replay the "
            "representative failed patch. Planning must choose a safer repair "
            "or a different reached strategy."
        )
    elif exact_replay_required:
        batch_rule = (
            f"Batch {batch_id} proved `{variant_id}` under build, full CEC, and "
            f"QoR gates. The Flow lane must replay `{winner_patch_rel}` exactly "
            "against the unchanged frozen baseline so this measured candidate "
            "enters the paired fan-in; do not redesign it."
        )
    else:
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
    negative_cec = (
        parse_whole_number(outcome_summary.get("cec_rejected_probe_count")) or 0
        if isinstance(outcome_summary, Mapping)
        else 0
    )
    planning_meta.update(
        {
            "task_type": (
                "repair" if not has_winner and negative_cec else "optimization"
            ),
            "target_command": command,
            "target_source_dir": target_source_dir,
            "should_skip_llm": False,
            "strategy_rationale": (
                (
                    f"Deterministic batch {batch_id} completed with no "
                    "correctness-backed eligible probe; consume its hard-gate "
                    "diagnostics and produce a safer repair or different strategy."
                )
                if not has_winner
                else (
                    f"Deterministic batch {batch_id} completed; use variant "
                    f"{variant_id} as measured sensitivity evidence."
                )
            ),
        }
    )
    assignment["_planning_meta"] = planning_meta
    assignment["planner_should_skip_llm"] = False
    assignment["target_command"] = command
    assignment["target_source_dir"] = target_source_dir
    diverse_frontier = winner_payload.get("diverse_frontier", [])
    frontier_text = json.dumps(diverse_frontier, sort_keys=True)[:5000]
    if not has_winner:
        measured_summary = (
            f"Deterministic batch search `{batch_id}` completed with no "
            "correctness-backed eligible probe. Its canonical negative summary "
            f"is {json.dumps(outcome_summary, sort_keys=True)[:5000]}. "
            f"Read `{outcome_evidence_rel}`, `{diagnostic_review_rel}`, and "
            f"`{diagnostic_cec_rel}`. The representative patch "
            f"`{diagnostic_patch_rel}` (sha256 `{diagnostic_patch_sha256}`) is "
            "failed diagnostic context only: do not replay it and do not use "
            "its unbacked QoR as reward. Propose a correctness-preserving repair "
            "or a materially different reached implementation strategy."
        )
        assignment["planner_hypothesis"] = measured_summary
    else:
        measured_summary = (
            f"Deterministic batch search `{batch_id}` completed. Best variant "
            f"`{variant_id}`: decision={winner.get('decision')}, average AND "
            f"improvement={winner.get('average_and_improve_pct')}, total AND "
            f"delta={winner.get('total_and_delta_candidate_minus_baseline')}, "
            f"improved/regressed={winner.get('improved_benchmark_count')}/"
            f"{winner.get('regressed_benchmark_count')}. Diverse top probes from "
            f"distinct command families: {frontier_text}."
        )
    if has_winner and exact_replay_required:
        assignment["planner_hypothesis"] = (
            "COORDINATOR-LOCKED PROMOTED BATCH REPLAY. "
            + measured_summary
            + f" The Flow candidate is the exact validated unified diff at "
            f"`{winner_patch_rel}` (sha256 `{winner_patch_sha256}`). Replay it "
            "unchanged against this round's frozen baseline; then repeat build, "
            "full CEC, and QoR so its real review participates in paired fan-in."
        )
    elif has_winner:
        assignment["planner_hypothesis"] = (
            measured_summary
            + f" Read `{summary_rel}` and `{winner_qor_rel}`. Use these "
            "measurements to propose a reached decision/scoring heuristic with "
            "a larger effect. Do not repeat a swept constant or enlarge an "
            "unproven capacity limit."
        )

    if has_winner and promoted and update_baseline:
        workspace = winner_workspace_path(assignment_path, winner_cycle)
        source_root = f"{workspace}/third_party/FlowTune/src"
        abc_bin = f"{source_root}/abc"
        baseline_ref = {
            "kind": "champion",
            "cycle_id": winner_cycle,
            "candidate_id": "candidate_001",
            "source_root": source_root,
            "abc_bin": abc_bin,
        }
        assignment.update(
            {
                "baseline_ref": baseline_ref,
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
    return winner_cycle if has_winner and promoted and update_baseline else ""


def bind_planner_batch_lineage_context(
    *,
    assignment_path: Path,
    assignment: dict[str, Any],
    target_command: str,
    lineage_context: Mapping[str, object] | None,
) -> tuple[dict[str, Any], str]:
    """Persist the coordinator inputs that are not embedded in assignments."""

    supplied = dict(lineage_context or {})
    reserved = {"schema_version", "variant_set", "target_command"}
    if reserved.intersection(supplied):
        raise ValueError("planner lineage context attempted to override reserved keys")
    desired: dict[str, object] = {
        **supplied,
        "schema_version": 1,
        "variant_set": "flow_wide",
        "target_command": target_command,
    }
    raw_existing = assignment.get("planner_batch_lineage_context")
    existing = dict(raw_existing) if isinstance(raw_existing, Mapping) else None
    if existing is not None:
        if supplied:
            differs = any(existing.get(key) != value for key, value in supplied.items())
            if differs:
                evidence = assignment.get("batch_search_evidence")
                if isinstance(evidence, Mapping):
                    raise ValueError(
                        "planner lineage changed while batch evidence is pending"
                    )
                existing = None
        if existing is not None:
            desired = existing
    effective_target = str(desired.get("target_command", "")).strip()
    if desired.get("schema_version") != 1:
        raise ValueError("planner batch context has the wrong schema_version")
    if desired.get("variant_set") != "flow_wide":
        raise ValueError("planner batch context has the wrong variant_set")
    if effective_target not in {"", "fx", "rewrite", "resub", "dc2", "csweep", "refactor"}:
        raise ValueError("planner batch context has an unsupported target command")
    if assignment.get("planner_batch_lineage_context") != desired:
        assignment = dict(assignment)
        assignment["planner_batch_lineage_context"] = desired
        temporary = assignment_path.with_suffix(
            assignment_path.suffix + ".planner-batch-lineage.tmp"
        )
        temporary.write_text(
            json.dumps(assignment, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(assignment_path)
    return assignment, effective_target


def validate_batch_winner(
    *,
    manifest: Mapping[str, object],
    winner_payload: dict[str, Any] | None,
    expected_lineage_hash: str,
) -> None:
    """Validate winner identity, allowing a canonical no-eligible outcome."""

    if winner_payload is None:
        raise ValueError("planner batch winner is missing or invalid JSON")
    if winner_payload.get("lineage_schema_version") != BATCH_LINEAGE_SCHEMA_VERSION:
        raise ValueError("planner batch winner lineage schema mismatch")
    if str(winner_payload.get("lineage_hash", "")) != expected_lineage_hash:
        raise ValueError("planner batch winner lineage mismatch")
    winner = winner_payload.get("winner")
    if winner is None:
        if bool(winner_payload.get("promotion_found")):
            raise ValueError("planner batch cannot promote without a winner row")
        return
    if not isinstance(winner, Mapping):
        raise ValueError("planner batch winner row is missing")
    cycle_id = str(winner.get("cycle_id", "")).strip()
    variant_id = str(winner.get("variant_id", "")).strip()
    batch_id = str(manifest.get("batch_id", "")).strip()
    if str(winner.get("batch_id", "")).strip() != batch_id:
        raise ValueError("planner batch winner batch_id mismatch")
    items = manifest.get("items")
    if not isinstance(items, list) or not any(
        isinstance(item, Mapping)
        and str(item.get("cycle_id", "")) == cycle_id
        and str(item.get("variant_id", "")) == variant_id
        for item in items
    ):
        raise ValueError("planner batch winner is not a manifest probe")


def _winner_manifest_item(
    manifest: Mapping[str, object],
    winner: Mapping[str, object],
) -> Mapping[str, object]:
    cycle_id = str(winner.get("cycle_id", "")).strip()
    variant_id = str(winner.get("variant_id", "")).strip()
    for item in manifest.get("items", ()):
        if (
            isinstance(item, Mapping)
            and str(item.get("cycle_id", "")).strip() == cycle_id
            and str(item.get("variant_id", "")).strip() == variant_id
        ):
            return item
    raise ValueError("planner batch winner has no bound manifest item")


def validate_planner_batch_manifest_identity(
    *,
    repo_root: Path,
    manifest: Mapping[str, object],
    batch_id: str,
    manifest_path: Path,
) -> None:
    if str(manifest.get("batch_id", "")) != batch_id:
        raise ValueError("planner batch manifest batch_id mismatch")
    expected_path = manifest_path.resolve().relative_to(repo_root.resolve()).as_posix()
    if str(manifest.get("manifest_path", "")) != expected_path:
        raise ValueError("planner batch manifest_path mismatch")


def winner_qor_path(assignment_path: Path, winner_cycle: str) -> str:
    """Resolve legacy or candidate-scoped probe QoR evidence."""

    resolved_assignment = assignment_path.resolve()
    try:
        repo_root = resolved_assignment.parents[4]
    except IndexError:
        return f"experiments/{winner_cycle}/impl_compare/comparison/qor_delta.csv"
    probe_assignment = (
        repo_root
        / "experiments"
        / winner_cycle
        / "agents"
        / "assignments"
        / "candidate_001.json"
    )
    if probe_assignment.is_file():
        context = CycleContext.from_assignment_file(repo_root, probe_assignment)
        return (
            impl_compare_root(context)
            / "comparison"
            / "qor_delta.csv"
        ).relative_to(repo_root).as_posix()
    return f"experiments/{winner_cycle}/impl_compare/comparison/qor_delta.csv"


def winner_workspace_path(assignment_path: Path, winner_cycle: str) -> str:
    """Resolve the promoted probe workspace under either artifact layout."""

    resolved_assignment = assignment_path.resolve()
    try:
        repo_root = resolved_assignment.parents[4]
    except IndexError:
        return (
            f"experiments/{winner_cycle}/impl_compare/"
            f"{IMPL_CANDIDATE_LABEL}/workspace"
        )
    probe_assignment = (
        repo_root
        / "experiments"
        / winner_cycle
        / "agents"
        / "assignments"
        / "candidate_001.json"
    )
    if probe_assignment.is_file():
        context = CycleContext.from_assignment_file(repo_root, probe_assignment)
        return (
            impl_compare_root(context) / IMPL_CANDIDATE_LABEL / "workspace"
        ).relative_to(repo_root).as_posix()
    return (
        f"experiments/{winner_cycle}/impl_compare/"
        f"{IMPL_CANDIDATE_LABEL}/workspace"
    )


def batch_variant_command(variant_id: str) -> str:
    if variant_id.startswith("csweep"):
        return "csweep"
    if variant_id.startswith("fx"):
        return "fx"
    for command in FLOW_SOURCE_TOUCHPOINTS:
        if variant_id.startswith(command):
            return command
    return "csweep"


def select_staged_planner_variants(
    variants: Sequence[PatchVariant],
    *,
    cycle_id: str,
    limit: int,
    enabled: bool,
) -> list[PatchVariant]:
    """Bound structural search cost while rotating across every Flow family.

    A full opt-only space currently contains 29 clean-build probes.  Running
    all of them serially before each paired round is operationally brittle.
    Structural cycles instead evaluate a deterministic 12-probe cross-family
    stage; cycle-to-cycle rotation covers the complete space by cycle 10.
    Targeted batches remain unchanged.
    """

    ordered = sorted(variants, key=lambda item: str(getattr(item, "variant_id", "")))
    if not enabled or limit < 1 or len(ordered) <= limit:
        return ordered
    groups: dict[str, list[PatchVariant]] = {}
    for variant in ordered:
        variant_id = str(getattr(variant, "variant_id", ""))
        groups.setdefault(batch_variant_command(variant_id), []).append(variant)
    families = sorted(groups)
    if limit < len(families):
        raise ValueError("planner batch limit cannot cover every command family")

    cycle_number = int(cycle_id.rsplit("_", 1)[1])
    stage = max(0, cycle_number - 6)
    base_quota, extra = divmod(limit, len(families))
    extra_families = {
        family
        for family, _items in sorted(
            groups.items(), key=lambda pair: (-len(pair[1]), pair[0])
        )[:extra]
    }
    selected: list[PatchVariant] = []
    for family in families:
        items = groups[family]
        quota = min(len(items), base_quota + (family in extra_families))
        start = (stage * max(1, quota)) % len(items)
        selected.extend(items[(start + offset) % len(items)] for offset in range(quota))

    # Small families may leave unused capacity. Fill it deterministically from
    # variants not already chosen, rotating the global order per stage.
    selected_ids = {str(getattr(item, "variant_id", "")) for item in selected}
    remaining = [
        item
        for item in ordered[stage % len(ordered) :] + ordered[: stage % len(ordered)]
        if str(getattr(item, "variant_id", "")) not in selected_ids
    ]
    selected.extend(remaining[: max(0, limit - len(selected))])
    return sorted(selected, key=lambda item: str(getattr(item, "variant_id", "")))


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
