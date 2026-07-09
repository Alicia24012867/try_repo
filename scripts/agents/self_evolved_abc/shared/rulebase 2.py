"""Self-Evolved Rulebase scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SelfEvolvedRulebase:
    """Small reproduction version of the paper's self-evolved rulebase."""

    path: Path
    text: str

    @classmethod
    def load(cls, path: Path) -> "SelfEvolvedRulebase":
        if path.exists():
            return cls(path=path, text=path.read_text(encoding="utf-8"))
        return cls(path=path, text="TODO_RULEBASE_BOOTSTRAP")

    def prompt_context(self) -> str:
        return self.text

    def propose_update(self, feedback_markdown: str) -> str:
        del feedback_markdown
        return "TODO_RULEBASE_UPDATE_FROM_FEEDBACK"

    def apply_update(self, update_text: str) -> None:
        del update_text
        raise NotImplementedError(
            "TODO_RULEBASE_APPLY: require human review before mutating rulebase."
        )

