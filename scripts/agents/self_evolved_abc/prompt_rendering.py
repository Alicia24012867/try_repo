""" Prompt template rendering helpers """

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Mapping

PLACEHOLDER_RE = re.compile(r"{{\s*([A-Z0-9_]+)\s*}}")

def load_template(repo_root: Path, relative_path: str) -> str:
    path = (repo_root / relative_path).resolve()
    _ensure_inside_repo(repo_root, path)
    return path.read_text(encoding="utf-8")

def rendering_templates(template: str, values: Mapping[str, object]) -> str:
    string_values = {key: _stringify(value) for key, value in values.items()}
    
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in string_values:
            return string_values[key]
        return match.group(0)
    
    return PLACEHOLDER_RE.sub(replace, template)

def find_unresolved_placeholders(text: str) -> tuple[str, ...]:
    return tuple(sorted(set(PLACEHOLDER_RE.findall(text))))

def compact_text_block(label: str, text: str, max_chars: int = 6000) -> str:
    cleaned = text.strip()
    if not cleaned:
        return f"{label}: empty"
    
    if len(cleaned) <= max_chars:
        return f"{label}:\n{cleaned}"
    
    head = cleaned[: max_chars // 2].rstrip()
    tail = cleaned[-max_chars // 2 :].lstrip()
    omitted = len(cleaned) - len(head) - len(tail)
    return(
        f"{label}:\n"
        f"{head}\n\n"
        f"...omitted {omitted} characters ... \n\n"
        f"{tail}"
    )
    
def summarize_csv(path: Path, max_rows: int = 20, max_chars: int = 10000) -> str:
    if not path.exists():
        return f"{path}: missing."
    
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        
    lines = [
        f"path: {path}",
        f"columns: {', '.join(reader.fieldnames or [])}",
        f"row_count: {len(rows)}",
        "",
        "sample_rows:",
    ]
    
    for index, row in enumerate(rows[: max_rows], start=1):
        cells = ", ".join(f"{key}={value}" for key, value in row.items())
        lines.append(f"{index}. {cells}")
        
    return compact_text_block("csv_summary", "\n".join(lines), max_chars=max_chars)

def summarize_flow_scripts(
    paths: tuple[Path, ...],
    max_files: int = 5,
    max_chars: int = 5000,
) -> str:
    chunks: list[str] = []
    
    for path in paths:
        if not path.exists():
            chunks.append(f"{path}: missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        chunks.append(f"path: {path}\n{text}")
        
    if not chunks:
        return "No previous flow scripts selected."
    
    return compact_text_block(
        "previous_flow_scripts",
        "\n\n---\n\n".join(chunks),
        max_chars=max_chars,
    )

def _ensure_inside_repo(repo_root: Path, path: Path) -> None:
    resolved_root = repo_root.resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {path}") from exc
    
def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {item}" for item in value)
    return str(value)