"""Regression coverage for hardening absorbed from correlation-relevance-plugin."""

from __future__ import annotations

import sys
import types

import pytest

from correlation_lib.lifecycle import LifecycleManager
from correlation_lib.rules import LifecycleState, load_rules_from_json


def _rule(rule_id: str = "rule-1", state: str = "proposal") -> dict:
    return {
        "id": rule_id,
        "trigger_context": "test",
        "trigger_keywords": ["keyword"],
        "must_also_fetch": ["context"],
        "relationship_type": "supports",
        "confidence": 0.8,
        "lifecycle": {"state": state},
    }


@pytest.mark.parametrize("rule_id", ["UPPERCASE", "1starts", "has space"])
def test_rule_ids_are_validated_at_load(rule_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid rule id"):
        load_rules_from_json([_rule(rule_id)])


def test_missing_required_field_is_reported_at_load() -> None:
    item = _rule()
    del item["trigger_context"]
    with pytest.raises(ValueError, match="missing required fields"):
        load_rules_from_json([item])


def test_hard_demote_records_reason_and_allows_validated_to_proposal() -> None:
    rule = load_rules_from_json([_rule(state="validated")]).rules[0]
    manager = LifecycleManager()

    new_state = manager.evaluate(rule, firing_count=100, effectiveness_ratio=0.1)

    assert new_state is LifecycleState.PROPOSAL
    assert "hard demote" in (manager.last_reason_for(rule.id) or "")


def test_legacy_adapter_exposes_initialization_health(monkeypatch: pytest.MonkeyPatch) -> None:
    memory_provider = types.ModuleType("agent.memory_provider")

    class MemoryProvider:
        pass

    memory_provider.MemoryProvider = MemoryProvider
    agent = types.ModuleType("agent")
    agent.memory_provider = memory_provider
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: "."
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", memory_provider)
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)

    import importlib

    module = importlib.import_module("correlation_lib_adapters.hermes.adapter")
    provider = module.CorrelationMemoryProvider()
    assert provider.is_healthy is False
    assert provider.last_init_error is None
