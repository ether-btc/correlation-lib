"""Hermes adapter — wires correlation-lib into Hermes Agent.

Provides:
- HermesRecallBackend: fetches context paths from Mnemosyne beam recall
- HermesContextBackend: injects correlated context into agent context

Used by CorrelatingMnemosyneProvider (composition_provider.py).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from correlation_lib.interfaces import ContextBackend, RecallBackend

if TYPE_CHECKING:
    from mnemosyne.core.beam import BeamMemory

logger = logging.getLogger(__name__)

# Limits for recall queries — control token budget per correlation path.
_RECALL_TOP_K = 3
_LOG_CONTENT_PREVIEW = 100


def _path_to_query(path: str) -> str:
    """Convert a context path to a Mnemosyne recall query.

    Paths are dot-separated or slash-separated identifiers.
    Examples:
        'backup-location' -> 'backup location'
        'rollback-instructions' -> 'rollback instructions'
        'config/backup' -> 'config backup'
    """
    return re.sub(r'[-_/]', ' ', path)


class HermesRecallBackend(RecallBackend):
    """Recall backend that fetches context paths via Mnemosyne beam recall.

    must_also_fetch entries are treated as search queries against Mnemosyne's
    working memory and episodic memory via beam.recall().

    IMPORTANT: This backend only READS from Mnemosyne (via beam.recall).
    It never writes via beam.remember — correlation is strictly READ-ONLY.
    """

    def __init__(self, beam: BeamMemory | None = None) -> None:
        # Named _beam for consistency — this is a BeamMemory instance,
        # not the MnemosyneMemoryProvider.
        self._beam = beam

    def set_mnemosyne(self, beam: BeamMemory) -> None:
        """Set or replace the BeamMemory instance after initialization."""
        self._beam = beam

    def fetch(self, path: str) -> str | None:
        """Fetch context for a path by querying Mnemosyne beam recall.

        Returns the best recall result as a formatted string, or None.
        """
        if self._beam is None:
            logger.warning("HermesRecallBackend: no BeamMemory instance — cannot fetch %r", path)
            return None

        query = _path_to_query(path)

        try:
            # READ-ONLY: use beam.recall(), NOT beam.remember()
            results = self._beam.recall(query, top_k=_RECALL_TOP_K)
            if not results:
                return None

            # Format results — beam.recall() returns List[Dict] with 'content' key
            formatted = []
            for i, result in enumerate(results, 1):
                if isinstance(result, dict):
                    content = result.get("content", "")
                    if content:
                        formatted.append(f"[{i}] {content}")
                elif isinstance(result, str):
                    formatted.append(f"[{i}] {result}")

            if formatted:
                return "\n\n".join(formatted)
            return None
        except Exception as exc:
            logger.error("HermesRecallBackend: recall failed for %r: %s", path, exc)
            return None


class HermesContextBackend(ContextBackend):
    """Context backend that injects correlated context into Hermes.

    In Hermes, context injection is done by appending to the agent's
    injected context list, which gets prepended to the system prompt
    or inserted as a context block before the next LLM call.
    """

    def __init__(self) -> None:
        self._injected: list[dict[str, Any]] = []

    def inject(self, content: str, source_rule: str, relationship: str) -> None:
        """Append correlated context to the injection buffer."""
        entry = {
            "content": content,
            "source_rule": source_rule,
            "relationship": relationship,
        }
        self._injected.append(entry)
        preview = content if len(content) <= _LOG_CONTENT_PREVIEW else content[:_LOG_CONTENT_PREVIEW] + "..."
        logger.debug(
            "Injected context from rule %s (relationship=%s): %s",
            source_rule, relationship, preview,
        )

    def get_injected(self) -> list[dict[str, Any]]:
        """Return all injected context entries."""
        return list(self._injected)

    def clear(self) -> None:
        """Clear the injection buffer (called after each turn)."""
        self._injected.clear()

    def format_injected(self) -> str:
        """Format injected context as a readable string for system prompt."""
        if not self._injected:
            return ""
        lines = ["## Correlated Context"]
        for entry in self._injected:
            lines.append(f"\n**Rule:** `{entry['source_rule']}`")
            lines.append(f"**Relationship:** {entry['relationship']}")
            lines.append(f"\n{entry['content']}")
        return "\n".join(lines)
