"""CorrelatingMnemosyneProvider — composition wrapper.

ONE memory provider registered with MemoryManager that internally wraps:
  - MnemosyneMemoryProvider (tools, writes, prefetch, lifecycle)
  - CorrelationEngine (READ-ONLY context enrichment)

All tool calls and writes go to Mnemosyne.  Correlation adds proactive
context enrichment in prefetch() and on_turn_start() without ever writing
to the memory store.

Safety guarantees:
  1. Enrichment is APPENDED — never replaces base Mnemosyne context
  2. Hard 500-token enrichment cap per turn
  3. Correlation is READ-ONLY — no beam writes, no state.db writes
  4. Mnemosyne initializes FIRST — correlation depends on its beam
  5. Graceful degradation — if correlation fails, Mnemosyne works alone
  6. No double-injection — single prefetch() path, single system_prompt_block()

Config (under memory.correlation in config.yaml):
  rule_file: str         # Path to JSON rule file (default: ~/.hermes/correlation-rules.json)
  watch_enabled: bool    # Hot-reload rules on change (default: false)
  db_path: str          # Effectiveness DB (default: ~/.hermes/correlation-effectiveness.db)
  enrichment_token_cap: int  # Max tokens for enrichment per turn (default: 500)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# Approximate chars-per-token for truncation heuristics
_CHARS_PER_TOKEN = 4
_DEFAULT_ENRICHMENT_TOKEN_CAP = 500


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Rough truncation by character count."""
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit("\n", 1)[0] + "\n...[truncated]"


class CorrelatingMnemosyneProvider(MemoryProvider):
    """Composition provider: Mnemosyne (base) + CorrelationEngine (enrichment).

    Registered as "enriched-mnemosyne".  MnemosyneMemoryProvider is instantiated
    internally — it is never registered separately with MemoryManager.
    """

    def __init__(self) -> None:
        self._mnemosyne: Optional[MemoryProvider] = None
        self._engine = None
        self._recall_backend = None
        self._context_backend = None
        self._correlation_ok = False
        self._turn_count = 0
        self._enrichment_cap = _DEFAULT_ENRICHMENT_TOKEN_CAP

    # -- Identity -----------------------------------------------------------

    @property
    def name(self) -> str:
        return "enriched-mnemosyne"

    # -- Core lifecycle -----------------------------------------------------

    def is_available(self) -> bool:
        """Available if Mnemosyne is available.

        Correlation is optional — graceful degradation.
        """
        try:
            self._ensure_mnemosyne()
            return self._mnemosyne.is_available()
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize Mnemosyne FIRST, then wire correlation on top."""
        # --- Phase 1: Mnemosyne (required) ---
        self._ensure_mnemosyne()
        self._mnemosyne.initialize(session_id, **kwargs)
        logger.info("CorrelatingMnemosyneProvider: Mnemosyne initialized OK")

        # --- Phase 2: Correlation (optional) ---
        try:
            self._init_correlation(**kwargs)
        except Exception as exc:
            logger.warning(
                "CorrelatingMnemosyneProvider: correlation init failed "
                "(degrading to Mnemosyne-only): %s", exc,
            )
            self._correlation_ok = False

    def _apply_enrichment_cap(self, raw: Any) -> None:
        """Validate and apply enrichment_token_cap from config."""
        try:
            cap = int(raw)
            if cap < 1:
                logger.warning("enrichment_token_cap=%d is too low, clamping to 1", cap)
                cap = 1
            elif cap > 2000:
                logger.warning("enrichment_token_cap=%d exceeds 2000, clamping", cap)
                cap = 2000
            self._enrichment_cap = cap
        except (TypeError, ValueError):
            logger.warning("Invalid enrichment_token_cap=%r, using default %d",
                           raw, _DEFAULT_ENRICHMENT_TOKEN_CAP)

    def system_prompt_block(self) -> str:
        """Mnemosyne's system prompt block only.  Correlation adds nothing here."""
        return self._forward("system_prompt_block")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Base Mnemosyne recall + correlation enrichment appended.

        Enrichment is capped at _enrichment_cap tokens.
        """
        # Base recall from Mnemosyne
        base = ""
        try:
            base = self._mnemosyne.prefetch(query, session_id=session_id)
        except Exception as exc:
            logger.error("Mnemosyne prefetch failed: %s", exc)

        if not self._correlation_ok:
            return base or ""

        # Correlation enrichment
        enrichment = self._correlation_prefetch(query)
        if not enrichment:
            return base or ""

        enrichment = _truncate_to_tokens(enrichment, self._enrichment_cap)

        # Append enrichment AFTER base context
        if base and base.strip():
            return f"{base}\n\n{enrichment}"
        return enrichment

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Forward to Mnemosyne only."""
        self._forward("queue_prefetch", query, session_id=session_id)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Forward to Mnemosyne only.  Correlation is READ-ONLY."""
        self._forward("sync_turn", user_content, assistant_content, session_id=session_id)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """All tools come from Mnemosyne.  Correlation has no tools."""
        return self._forward("get_tool_schemas")

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """All tool calls go to Mnemosyne."""
        return self._forward("handle_tool_call", tool_name, args, **kwargs)

    def shutdown(self) -> None:
        """Shut down correlation first, then Mnemosyne (reverse init order)."""
        if self._correlation_ok:
            try:
                if self._context_backend:
                    self._context_backend.clear()
                self._recall_backend = None
                self._engine = None
                self._correlation_ok = False
            except Exception as exc:
                logger.warning("Correlation shutdown error: %s", exc)

        if self._mnemosyne:
            try:
                self._mnemosyne.shutdown()
            except Exception as exc:
                logger.warning("Mnemosyne shutdown error: %s", exc)

    # -- Optional hooks (forward to Mnemosyne, add correlation triggers) ---

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Forward to Mnemosyne + correlation new-task detection."""
        # Mnemosyne lifecycle
        try:
            self._mnemosyne.on_turn_start(turn_number, message, **kwargs)
        except Exception as exc:
            logger.warning("Mnemosyne on_turn_start error: %s", exc)

        if not self._correlation_ok:
            return

        self._turn_count += 1

        # Clear previous enrichment
        if self._context_backend:
            self._context_backend.clear()

        # Fire correlation on new-task detection
        try:
            from correlation_lib import Enricher
            if not Enricher.is_new_task(message):
                return

            if self._engine and self._engine.enricher:
                result = self._engine.enricher.on_task_start(message)
                if result.had_errors:
                    for err in result.errors:
                        logger.warning("Correlation enrichment error: %s", err)
                if result.injected_count > 0:
                    logger.info(
                        "Correlation fired on turn %d: %d rules, %d injections",
                        self._turn_count, len(result.fired_rules), result.injected_count,
                    )
        except Exception as exc:
            logger.warning("Correlation on_turn_start error: %s", exc)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Forward to Mnemosyne."""
        self._forward("on_session_end", messages)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                          reset: bool = False, **kwargs) -> None:
        """Forward to Mnemosyne."""
        try:
            self._mnemosyne.on_session_switch(
                new_session_id, parent_session_id=parent_session_id,
                reset=reset, **kwargs,
            )
        except AttributeError:
            pass  # Mnemosyne may not implement this method
        except Exception as exc:
            logger.warning("on_session_switch error: %s", exc)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Forward to Mnemosyne."""
        try:
            return self._mnemosyne.on_pre_compress(messages)
        except Exception:
            return ""

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[Dict[str, Any]] = None) -> None:
        """Forward to Mnemosyne."""
        try:
            self._mnemosyne.on_memory_write(action, target, content, metadata)
        except Exception:
            pass

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        """Forward to Mnemosyne."""
        try:
            self._mnemosyne.on_delegation(task, result, child_session_id=child_session_id, **kwargs)
        except Exception:
            pass

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """Merge Mnemosyne config + correlation config."""
        schemas = []
        try:
            schemas.extend(self._mnemosyne.get_config_schema())
        except Exception:
            pass
        schemas.extend([
            {
                "key": "rule_file",
                "description": "Path to correlation JSON rule file",
                "required": False,
                "default": "~/.hermes/correlation-rules.json",
            },
            {
                "key": "watch_enabled",
                "description": "Hot-reload correlation rules on file change",
                "required": False,
                "default": False,
            },
            {
                "key": "db_path",
                "description": "Path to correlation effectiveness SQLite DB",
                "required": False,
                "default": "~/.hermes/correlation-effectiveness.db",
            },
            {
                "key": "enrichment_token_cap",
                "description": "Max tokens for correlation enrichment per turn",
                "required": False,
                "default": _DEFAULT_ENRICHMENT_TOKEN_CAP,
            },
        ])
        return schemas

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Forward to Mnemosyne."""
        try:
            self._mnemosyne.save_config(values, hermes_home)
        except Exception:
            pass

    # -- Internal helpers ---------------------------------------------------

    def _ensure_mnemosyne(self) -> None:
        """Lazy-load MnemosyneMemoryProvider exactly once."""
        if self._mnemosyne is not None:
            return
        # Import from user-installed plugin at ~/.hermes/plugins/mnemosyne/
        import importlib.util
        plugin_dir = Path.home() / ".hermes" / "plugins" / "mnemosyne"
        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            raise RuntimeError(
                f"Mnemosyne plugin not found at {plugin_dir}. "
                "Install it first: ~/.hermes/plugins/mnemosyne/"
            )
        spec = importlib.util.spec_from_file_location("mnemosyne_plugin", str(init_file))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self._mnemosyne = mod.MnemosyneMemoryProvider()
        logger.info("CorrelatingMnemosyneProvider: loaded MnemosyneMemoryProvider from %s", plugin_dir)

    def _init_correlation(self, **kwargs) -> None:
        """Initialize the correlation engine (Phase 2, after Mnemosyne)."""
        from hermes_constants import get_hermes_home

        hermes_home = kwargs.get("hermes_home") or str(get_hermes_home())
        config_path = Path(hermes_home) / "config.yaml"

        # Defaults
        rule_file = Path(hermes_home) / "correlation-rules.json"
        watch_enabled = False
        db_path = Path(hermes_home) / "correlation-effectiveness.db"

        # Load config overrides
        if config_path.exists():
            import yaml
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            corr_cfg = (config.get("memory", {}).get("correlation") or {})
            if corr_cfg.get("rule_file"):
                rule_file = Path(corr_cfg["rule_file"]).expanduser()
            watch_enabled = bool(corr_cfg.get("watch_enabled", False))
            if corr_cfg.get("db_path"):
                db_path = Path(corr_cfg["db_path"]).expanduser()
            if corr_cfg.get("enrichment_token_cap"):
                self._apply_enrichment_cap(corr_cfg["enrichment_token_cap"])

        # Import correlation-lib
        from correlation_lib import create_engine
        from correlation_lib_adapters.hermes.backends import (
            HermesContextBackend,
            HermesRecallBackend,
        )

        self._recall_backend = HermesRecallBackend()
        self._context_backend = HermesContextBackend()

        self._engine = create_engine(
            rule_file=str(rule_file) if rule_file.exists() else None,
            watch_enabled=watch_enabled,
            db_path=str(db_path),
            recall_backend=self._recall_backend,
            context_backend=self._context_backend,
        )

        # Wire Mnemosyne beam into recall backend
        self._wire_mnemosyne_to_recall()

        self._correlation_ok = True
        logger.info(
            "CorrelatingMnemosyneProvider: correlation engine initialized "
            "(rule_file=%s, watch=%s, cap=%d tokens)",
            rule_file, watch_enabled, self._enrichment_cap,
        )

    def _wire_mnemosyne_to_recall(self) -> None:
        """Try to wire the Mnemosyne beam into the recall backend.

        Non-fatal if beam is not yet available — prefetch will still work
        for base recall; correlation just won't resolve must_also_fetch paths.

        Note: accesses MnemosyneMemoryProvider._beam private attribute.
        This is a known coupling point — if Mnemosyne renames this attribute,
        correlation will degrade gracefully with a logged warning.
        """
        if not self._recall_backend:
            return
        try:
            beam = getattr(self._mnemosyne, "_beam", None)
            if beam is not None:
                self._recall_backend.set_mnemosyne(beam)
                logger.info("CorrelatingMnemosyneProvider: wired Mnemosyne beam to recall backend")
            else:
                logger.warning(
                    "CorrelatingMnemosyneProvider: Mnemosyne _beam is None — "
                    "correlation must_also_fetch will not resolve. "
                    "This is expected if Mnemosyne is still initializing."
                )
        except Exception as exc:
            logger.warning("Failed to wire Mnemosyne beam: %s", exc)

    def _correlation_prefetch(self, query: str) -> str:
        """Run correlation enrichment and return formatted text."""
        if not self._engine or not self._engine.enricher:
            return ""
        if not query or not query.strip():
            return ""

        try:
            result = self._engine.enricher.on_prefetch(query)
            if result.had_errors:
                for err in result.errors:
                    logger.warning("Correlation prefetch error: %s", err)
            if self._context_backend:
                return self._context_backend.format_injected()
            return ""
        except Exception as exc:
            logger.error("Correlation prefetch failed: %s", exc)
            return ""

    # Methods expected to return lists — used as fallbacks in _forward
    _LIST_RETURNING_METHODS = frozenset({"get_tool_schemas"})

    def _forward(self, method_name: str, *args, **kwargs) -> Any:
        """Forward a method call to the wrapped Mnemosyne provider."""
        if self._mnemosyne is None:
            self._ensure_mnemosyne()
        method = getattr(self._mnemosyne, method_name, None)
        if method is None:
            if method_name in self._LIST_RETURNING_METHODS:
                return []
            return None
        return method(*args, **kwargs)
