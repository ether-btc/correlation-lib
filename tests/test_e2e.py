#!/usr/bin/env python3
"""E2E integration test for correlation-lib.

Exercises the full chain:
  task_text → matcher → recall_backend → context injection → effectiveness tracking

Run: python tests/test_e2e.py
"""

import tempfile
from pathlib import Path

from correlation_lib import create_engine
from correlation_lib.interfaces import ContextBackend, RecallBackend
from correlation_lib.rules import CorrelationRule, RuleSet

# --------------------------------------------------------------------------- #
# Mock backends — simulate Hermes/Mnemosyne behavior
# --------------------------------------------------------------------------- #

class MockRecallBackend(RecallBackend):
    """Returns canned context for demo purposes (simulates Mnemosyne recall)."""

    _contexts: dict[str, str] = {
        "backup-location": (
            "Backup is stored at: /backup/daily on USB drive (mounted at /mnt/usb-backup). "
            "Last successful backup: 2026-05-16 23:00 UTC. Retention: 30 days."
        ),
        "rollback-instructions": (
            "To rollback: (1) stop service: sudo systemctl stop hermes "
            "(2) restore: cp /backup/daily/config.yaml ~/.hermes/config.yaml "
            "(3) restart: sudo systemctl start hermes"
        ),
        "recent-incidents": (
            "2026-05-15: Database lock contention caused 2s latency spike. "
            "Fixed by adding index on sessions(updated_at). "
            "2026-05-14: Memory leak in mnemosyne consolidation. "
            "Fixed by upgrading to v2.8.1."
        ),
        "error-patterns": (
            "Common patterns: (1) PermissionError on ~/.hermes paths — "
            "hermes runs as different user than file owner. "
            "(2) sqlite3.OperationalError: database is locked — "
            "concurrent writes from multiple processes."
        ),
        "backup-verification": (
            "Before migration: run 'SELECT COUNT(*) FROM users' to get row count. "
            "After migration: verify count matches. "
            "Check for orphaned records in related tables."
        ),
        "rollback-runbook": (
            "Rollback procedure: (1) Stop service: sudo systemctl stop hermes "
            "(2) Restore last backup: cp /backup/daily/users_backup.sql ~/.hermes/ "
            "(3) Run: psql hermes < ~/.hermes/users_backup.sql "
            "(4) Restart: sudo systemctl start hermes"
        ),
        "memory-history": (
            "Memory usage history (last 7 days): "
            "2026-05-16: 68% used, 2026-05-15: 72% used (peak), "
            "2026-05-14: 45% used (after restart). "
            "Baseline: 55-65% during idle."
        ),
        "gc-logs": (
            "GC log summary: No Full GC events in 30 days. "
            "Minor GC runs every ~45min, avg 12ms pause. "
            "Last OOM: 2026-05-14 (mnemosyne consolidation, fixed in v2.8.1)."
        ),
    }

    def fetch(self, path: str) -> str | None:
        return self._contexts.get(path)


class MockContextBackend(ContextBackend):
    """No-op context backend — context is injected into task output, not stored."""

    def inject(self, content: str, source_rule: str, relationship: str) -> None:
        pass  # In real Hermes backend this would append to context stream


# --------------------------------------------------------------------------- #
# Test cases
# --------------------------------------------------------------------------- #

def test_config_change_rule():
    """CR-001 fires for 'modify config' → fetches backup-location + rollback-instructions."""
    engine = create_engine(
        rule_file=Path("examples/example_rules.json"),
        recall_backend=MockRecallBackend(),
        context_backend=MockContextBackend(),
    )
    assert engine.enricher is not None, "Enricher should be initialized with backends"

    result = engine.enricher.on_task_start("modify the hermes config to use new model")

    assert result.injected_count == 2, f"Expected 2 injections, got {result.injected_count}"
    fired_ids = [rule.id for rule, _ in result.fired_rules]
    assert "cr-001" in fired_ids, f"cr-001 should fire, got {fired_ids}"
    assert not result.had_errors, f"Unexpected errors: {result.errors}"


def test_error_debugging_rule():
    """CR-002 fires for 'debug the error crash' → fetches recent-incidents + error-patterns."""
    engine = create_engine(
        rule_file=Path("examples/example_rules.json"),
        recall_backend=MockRecallBackend(),
        context_backend=MockContextBackend(),
    )

    result = engine.enricher.on_task_start("debug the error crash in the terminal")

    assert result.injected_count == 2, f"Expected 2 injections, got {result.injected_count}"
    fired_ids = [rule.id for rule, _ in result.fired_rules]
    assert "cr-002" in fired_ids, f"cr-002 should fire, got {fired_ids}"
    assert not result.had_errors, f"Unexpected errors: {result.errors}"


def test_database_migration_rule():
    """CR-003 fires for 'migrate database schema' → fetches backup-verification + rollback-runbook."""
    engine = create_engine(
        rule_file=Path("examples/example_rules.json"),
        recall_backend=MockRecallBackend(),
        context_backend=MockContextBackend(),
    )

    result = engine.enricher.on_task_start("migrate the database schema to v2")

    assert result.injected_count == 2, f"Expected 2 injections, got {result.injected_count}"
    fired_ids = [rule.id for rule, _ in result.fired_rules]
    assert "cr-003" in fired_ids, f"cr-003 should fire, got {fired_ids}"
    assert not result.had_errors, f"Unexpected errors: {result.errors}"


def test_memory_optimization_rule():
    """CR-004 fires for 'check memory leak' → fetches memory-history + gc-logs."""
    engine = create_engine(
        rule_file=Path("examples/example_rules.json"),
        recall_backend=MockRecallBackend(),
        context_backend=MockContextBackend(),
    )

    result = engine.enricher.on_task_start("check memory usage for the cache process")

    assert result.injected_count == 2, f"Expected 2 injections, got {result.injected_count}"
    fired_ids = [rule.id for rule, _ in result.fired_rules]
    assert "cr-004" in fired_ids, f"cr-004 should fire, got {fired_ids}"
    assert not result.had_errors, f"Unexpected errors: {result.errors}"


def test_no_match_no_injection():
    """Non-matching task fires no rules, yields zero injections."""
    engine = create_engine(
        rule_file=Path("examples/example_rules.json"),
        recall_backend=MockRecallBackend(),
        context_backend=MockContextBackend(),
    )

    result = engine.enricher.on_task_start("what is the weather today")

    assert result.injected_count == 0, f"Expected 0 injections, got {result.injected_count}"
    assert len(result.fired_rules) == 0, f"Expected no fired rules, got {len(result.fired_rules)}"
    assert not result.had_errors, f"Unexpected errors: {result.errors}"


def test_effectiveness_tracking():
    """Rule effectiveness is recorded in the tracker after enrichment."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    engine = create_engine(
        rule_file=Path("examples/example_rules.json"),
        recall_backend=MockRecallBackend(),
        context_backend=MockContextBackend(),
        db_path=db_path,
    )

    # Fire cr-002
    result = engine.enricher.on_task_start("debug the error crash in the terminal")
    assert result.injected_count == 2

    # Check that tracker recorded it via the store directly
    stats = engine.tracker._store.get_stats("cr-002")
    assert stats, "cr-002 should have stats after firing"
    assert stats["firing_count"] == 1, f"Expected 1 fire for cr-002, got {stats['firing_count']}"

    db_path.unlink(missing_ok=True)


def test_multiple_rules_fire():
    """When multiple rules match the same task, all fire and inject."""
    # Build rule set directly (no JSON file needed)
    rules = RuleSet()
    rules.add(CorrelationRule(
        id="multi-1",
        trigger_context="debug-context",
        trigger_keywords=("debug", "memory"),
        must_also_fetch=("memory-history",),
        relationship_type="related_to",
        confidence=0.8,
    ))
    rules.add(CorrelationRule(
        id="multi-2",
        trigger_context="optimization",
        trigger_keywords=("memory", "usage"),
        must_also_fetch=("gc-logs",),
        relationship_type="related_to",
        confidence=0.85,
    ))

    from correlation_lib.enricher import Enricher
    from correlation_lib.lifecycle import LifecycleManager
    from correlation_lib.tracker import EffectivenessTracker, SQLiteEffectivenessStore

    store = SQLiteEffectivenessStore()
    tracker = EffectivenessTracker(store)
    lifecycle_manager = LifecycleManager()
    enricher = Enricher(rules, MockRecallBackend(), MockContextBackend(), tracker, lifecycle_manager)

    result = enricher.on_task_start("debug memory usage")

    fired_ids = [rule.id for rule, _ in result.fired_rules]
    assert "multi-1" in fired_ids, f"multi-1 should fire, got {fired_ids}"
    assert "multi-2" in fired_ids, f"multi-2 should fire, got {fired_ids}"
    assert result.injected_count == 2, f"Expected 2 injections, got {result.injected_count}"


if __name__ == "__main__":
    import sys

    tests = [
        test_config_change_rule,
        test_error_debugging_rule,
        test_database_migration_rule,
        test_memory_optimization_rule,
        test_no_match_no_injection,
        test_effectiveness_tracking,
        test_multiple_rules_fire,
    ]

    failed = 0
    for test in tests:
        try:
            print(f"RUNNING {test.__name__}...", end=" ", flush=True)
            test()
            print("PASS")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"FAIL: {e}")
            failed += 1

    print()
    if failed:
        print(f"{failed}/{len(tests)} tests FAILED")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests PASSED")
