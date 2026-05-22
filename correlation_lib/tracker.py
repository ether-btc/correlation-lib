"""Effectiveness tracking with SQLite persistence.

Q4=A confirmed: SQLite standalone at ~/.hermes/correlation-effectiveness.db
Q1=A confirmed: fully automated lifecycle advancement.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from correlation_lib.interfaces import EffectivenessStore
from correlation_lib.lifecycle import LifecycleManager
from correlation_lib.rules import LifecycleState, RuleSet

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".hermes" / "correlation-effectiveness.db"

# Production-grade SQLite pragmas for WAL mode, concurrency, and reliability.
_PRAGMAS = [
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA foreign_keys = ON",
    "PRAGMA cache_size = -64000",
    "PRAGMA temp_store = MEMORY",
]


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply production pragmas to a new connection."""
    for pragma in _PRAGMAS:
        conn.execute(pragma)


@dataclass
class RuleStats:
    """Aggregated effectiveness stats for a rule."""

    rule_id: str
    firing_count: int
    relevance_count: int
    irrelevance_count: int
    effectiveness_ratio: float
    last_fired: str | None
    last_relevance_recorded: str | None
    current_state: LifecycleState


class SQLiteEffectivenessStore(EffectivenessStore):
    """SQLite-backed effectiveness store (Q4=A).

    Thread-safe via a single shared connection with a lock.
    Uses WAL mode for better concurrency under concurrent reads/writes.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Return the shared, pragma-configured connection (thread-safe).

        Must hold self._lock before calling.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path,
                timeout=30.0,
                check_same_thread=False,
                isolation_level="DEFERRED",
            )
            self._conn.row_factory = sqlite3.Row
            _configure_connection(self._conn)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rule_effectiveness (
                    rule_id TEXT PRIMARY KEY,
                    firing_count INTEGER DEFAULT 0,
                    relevance_count INTEGER DEFAULT 0,
                    irrelevance_count INTEGER DEFAULT 0,
                    last_fired TEXT,
                    last_relevance_recorded TEXT,
                    current_state TEXT DEFAULT 'proposal'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lifecycle_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    triggered_by TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lifecycle_rule
                ON lifecycle_log(rule_id, timestamp)
            """)
            conn.commit()

    def record_fire(self, rule_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO rule_effectiveness (rule_id, firing_count, last_fired)
                VALUES (?, 1, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    firing_count = firing_count + 1,
                    last_fired = excluded.last_fired
                """,
                (rule_id, now),
            )
            conn.commit()

    def record_relevance(self, rule_id: str, is_relevant: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        col = "relevance_count" if is_relevant else "irrelevance_count"
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                f"""
                INSERT INTO rule_effectiveness (rule_id, {col}, last_relevance_recorded)
                VALUES (?, 1, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    {col} = {col} + 1,
                    last_relevance_recorded = excluded.last_relevance_recorded
                """,
                (rule_id, now),
            )
            conn.commit()

    def get_stats(self, rule_id: str) -> dict:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM rule_effectiveness WHERE rule_id = ?", (rule_id,),
            ).fetchone()
        if not row:
            return {}
        cols = ["rule_id", "firing_count", "relevance_count", "irrelevance_count",
                "last_fired", "last_relevance_recorded", "current_state"]
        return dict(zip(cols, row))

    def get_all_stats(self) -> dict[str, dict]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute("SELECT * FROM rule_effectiveness").fetchall()
        cols = ["rule_id", "firing_count", "relevance_count", "irrelevance_count",
                "last_fired", "last_relevance_recorded", "current_state"]
        return {row[0]: dict(zip(cols, row)) for row in rows}

    def update_state(self, rule_id: str, state: LifecycleState) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE rule_effectiveness SET current_state = ? WHERE rule_id = ?",
                (state.value, rule_id),
            )
            conn.commit()

    def log_lifecycle(
        self,
        rule_id: str,
        from_state: LifecycleState,
        to_state: LifecycleState,
        reason: str,
        triggered_by: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO lifecycle_log (rule_id, from_state, to_state, reason, triggered_by, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rule_id, from_state.value, to_state.value, reason, triggered_by, now),
            )
            conn.commit()


class EffectivenessTracker:
    """Tracks rule effectiveness and drives automated lifecycle transitions.

    Q1=A confirmed: fully automated — auto-promote AND auto-demote.
    """

    def __init__(self, store: EffectivenessStore) -> None:
        self._store = store

    def record(self, rule_id: str, was_relevant: bool | None = None) -> None:
        """Record a rule firing, optionally with relevance feedback.

        Args:
            rule_id: The rule that fired.
            was_relevant: If None, fire is recorded but no relevance judgment made.
                         If True/False, both fire and relevance are recorded.
        """
        self._store.record_fire(rule_id)
        if was_relevant is not None:
            self._store.record_relevance(rule_id, was_relevant)

    def get_effectiveness_ratio(self, rule_id: str) -> float:
        """Compute effectiveness ratio: relevant / (relevant + irrelevant)."""
        stats = self._store.get_stats(rule_id)
        rel = stats.get("relevance_count", 0)
        irel = stats.get("irrelevance_count", 0)
        total = rel + irel
        return rel / total if total > 0 else 0.0

    def evaluate_lifecycles(self, ruleset: RuleSet, lifecycle_manager: LifecycleManager) -> None:
        """Run lifecycle evaluation on all tracked rules.

        Q1=A: fully automated — no human intervention required.
        Called after rule firings are recorded.
        """
        all_stats = self._store.get_all_stats()
        for rule in ruleset.get_active_rules():
            if not lifecycle_manager.can_advance(rule):
                continue
            raw = all_stats.get(rule.id)
            if not raw:
                continue
            rel = raw.get("relevance_count", 0)
            irel = raw.get("irrelevance_count", 0)
            total = rel + irel
            eff_ratio = rel / total if total > 0 else 0.0
            new_state = lifecycle_manager.evaluate(
                rule,
                firing_count=raw.get("firing_count", 0),
                effectiveness_ratio=eff_ratio,
            )
            if new_state:
                # Update rule in ruleset using replace() for frozen dataclass safety
                new_rules = ruleset.with_lifecycle_update(rule.id, new_state)
                ruleset.rules = new_rules
                # Update store
                self._store.update_state(rule.id, new_state)
                # Log to lifecycle log
                self._store.log_lifecycle(
                    rule.id,
                    rule.lifecycle_state,
                    new_state,
                    f"auto: firing_count={raw.get('firing_count', 0)}, eff_ratio={eff_ratio:.3f}",
                    "auto",
                )

    def get_stats(self, rule_id: str) -> RuleStats:
        """Get comprehensive stats for a rule."""
        raw = self._store.get_stats(rule_id)
        if not raw:
            raise KeyError(f"No stats found for rule {rule_id}")
        rel = raw.get("relevance_count", 0)
        irel = raw.get("irrelevance_count", 0)
        total = rel + irel
        eff_ratio = rel / total if total > 0 else 0.0
        return RuleStats(
            rule_id=rule_id,
            firing_count=raw.get("firing_count", 0),
            relevance_count=rel,
            irrelevance_count=irel,
            effectiveness_ratio=eff_ratio,
            last_fired=raw.get("last_fired"),
            last_relevance_recorded=raw.get("last_relevance_recorded"),
            current_state=LifecycleState(raw.get("current_state", "proposal")),
        )

    def get_all_stats(self) -> dict[str, RuleStats]:
        """Get stats for all rules."""
        result = {}
        for rule_id, raw in self._store.get_all_stats().items():
            rel = raw.get("relevance_count", 0)
            irel = raw.get("irrelevance_count", 0)
            total = rel + irel
            eff_ratio = rel / total if total > 0 else 0.0
            result[rule_id] = RuleStats(
                rule_id=rule_id,
                firing_count=raw.get("firing_count", 0),
                relevance_count=rel,
                irrelevance_count=irel,
                effectiveness_ratio=eff_ratio,
                last_fired=raw.get("last_fired"),
                last_relevance_recorded=raw.get("last_relevance_recorded"),
                current_state=LifecycleState(raw.get("current_state", "proposal")),
            )
        return result
