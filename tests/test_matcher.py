"""Tests for correlation_lib.matcher."""

import re

import pytest

from correlation_lib.matcher import (
    Matcher,
    filter_high_confidence,
    get_fired_rules,
)
from correlation_lib.rules import CorrelationRule, LifecycleState, MatchMode, RuleSet


def make_rule(
    id: str,
    trigger_context: str,
    keywords: list[str],
    confidence: float,
    match_mode: MatchMode = MatchMode.AUTO,
) -> CorrelationRule:
    return CorrelationRule(
        id=id,
        trigger_context=trigger_context,
        trigger_keywords=tuple(keywords),
        must_also_fetch=("ctx-" + id,),
        relationship_type="constrains",
        confidence=confidence,
        match_mode=match_mode,
    )


def make_ruleset(rules: list[CorrelationRule]) -> RuleSet:
    rs = RuleSet()
    for r in rules:
        rs.add(r)
    return rs


class ReferenceMatcher(Matcher):
    """Literal pre-optimization keyword matcher used as a differential oracle."""

    def _match_keywords(
        self,
        rule: CorrelationRule,
        task_words: set[str],
        task_lower: str,
        task_text: str,
    ) -> tuple[list[str], float]:
        matched: list[str] = []

        if rule.match_mode == MatchMode.STRICT:
            for kw in rule.trigger_keywords:
                pattern = r"\b" + re.escape(kw.lower()) + r"\b"
                if re.search(pattern, task_lower):
                    matched.append(kw)
        elif rule.match_mode == MatchMode.LENIENT:
            for kw in rule.trigger_keywords:
                if kw.lower() in task_lower:
                    matched.append(kw)
        else:
            for kw in rule.trigger_keywords:
                pattern = r"\b" + re.escape(kw.lower()) + r"\b"
                if re.search(pattern, task_lower):
                    matched.append(kw)
                elif kw.lower() in task_lower:
                    matched.append(kw)

        coverage = len(matched) / len(rule.trigger_keywords) if rule.trigger_keywords else 0.0
        return matched, coverage


def result_signature(results: list[object]) -> list[tuple[object, ...]]:
    return [
        (
            result.rule,
            result.matched_keywords,
            result.keyword_coverage,
            result.context_score,
            result.combined_score,
            result.is_match,
        )
        for result in results
    ]


class TestMatcher:
    def test_match_basic(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "config-change", ["config", "setting"], 0.95),
        ])
        matcher = Matcher(ruleset)
        results = matcher.match("I need to modify the config setting for the gateway")
        assert len(results) == 1
        assert results[0].rule.id == "cr-001"
        assert results[0].is_match
        assert "config" in results[0].matched_keywords
        assert "setting" in results[0].matched_keywords

    def test_match_no_match(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "config-change", ["config", "setting"], 0.95),
        ])
        matcher = Matcher(ruleset)
        results = matcher.match("debug the memory leak in the cache")
        assert len(results) == 0 or not any(r.is_match for r in results)

    def test_match_with_context_hint(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "config-change", ["config"], 0.95),
            make_rule("cr-002", "error-debug", ["error"], 0.85),
        ])
        matcher = Matcher(ruleset)
        # With context hint, only matching context rules are considered
        results = matcher.match("fix the config error", trigger_context="config-change")
        assert all(r.rule.trigger_context == "config-change" for r in results if r.is_match)

    def test_match_strict_mode(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "test", ["config"], 0.95, match_mode=MatchMode.STRICT),
        ])
        matcher = Matcher(ruleset)
        # Should NOT match "reconfigure" (substring but not word-boundary)
        results = matcher.match("reconfigure the gateway")
        assert not any(r.is_match for r in results)
        # Should match "config" as word
        results = matcher.match("the config file")
        assert any(r.is_match for r in results)

    def test_match_lenient_mode(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "test", ["config"], 0.95, match_mode=MatchMode.LENIENT),
        ])
        matcher = Matcher(ruleset)
        # Should match "reconfigure" (substring)
        results = matcher.match("reconfigure the gateway")
        assert any(r.is_match for r in results)

    def test_match_keyword_coverage(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "test", ["alpha", "beta", "gamma"], 0.95),
        ])
        matcher = Matcher(ruleset)
        # Partial match: only "beta" present
        results = matcher.match("the beta release")
        assert len(results) == 1
        assert results[0].keyword_coverage == pytest.approx(1/3, rel=0.01)
        assert results[0].matched_keywords == ("beta",)
        # is_match depends on combined_score threshold; partial match has low score
        # so is_match=False here is expected behavior — not a bug

    def test_match_combined_score(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "config-change", ["config"], 0.80),
        ])
        matcher = Matcher(ruleset)
        results = matcher.match("config file modified")
        assert len(results) == 1
        # Score should be influenced by rule confidence
        assert results[0].combined_score <= 0.80

    def test_match_retired_rules_excluded(self) -> None:
        # Create rule already in RETIRED state via constructor
        retired_rule = CorrelationRule(
            id="cr-001",
            trigger_context="test",
            trigger_keywords=("test",),
            must_also_fetch=("ctx",),
            relationship_type="constrains",
            confidence=0.95,
            lifecycle_state=LifecycleState.RETIRED,
        )
        ruleset = make_ruleset([retired_rule])
        matcher = Matcher(ruleset)
        results = matcher.match("test task")
        # RETIRED rules should not appear in results at all
        assert not any(r.rule.id == "cr-001" and r.is_match for r in results)


class TestMatcherOptimizationOracle:
    @pytest.mark.parametrize("mode", list(MatchMode))
    @pytest.mark.parametrize(
        "task_text",
        [
            "CONFIG config reconfigure C++ a.b \\\\ path",
            "İ i\u0307 ı ß STRASSE 中文 中文词 😀 e\u0301 é",
            "punctuation: (alpha), snake_case and emoji😀tail",
            "",
        ],
    )
    def test_exact_differential_edge_matrix(self, mode: MatchMode, task_text: str) -> None:
        rules = [
            make_rule(
                "edge-a",
                "config-change",
                ["Config", "config", "C++", "a.b", "\\", "", "Config"],
                0.91,
                mode,
            ),
            make_rule(
                "edge-b",
                "unicode-context",
                ["İ", "i\u0307", "ß", "STRASSE", "中文", "中文词", "😀", "e\u0301", "é"],
                0.83,
                mode,
            ),
            make_rule("edge-empty", "", [], 0.77, mode),
            make_rule("tie-a", "stable-tie", ["same"], 0.8, mode),
            make_rule("tie-b", "stable-tie", ["same"], 0.8, mode),
        ]
        ruleset = make_ruleset(rules)

        expected = ReferenceMatcher(ruleset).match(task_text)
        actual_matcher = Matcher(ruleset)
        assert result_signature(actual_matcher.match(task_text)) == result_signature(expected)
        assert result_signature(actual_matcher.match(task_text)) == result_signature(expected)

    def test_live_ruleset_add_replace_retire_and_context_hint(self) -> None:
        original = make_rule("live", "first-context", ["First"], 0.9)
        ruleset = make_ruleset([original])
        actual = Matcher(ruleset)
        reference = ReferenceMatcher(ruleset)

        for text, hint in [("First", None), ("First", "first-context")]:
            assert result_signature(actual.match(text, hint)) == result_signature(reference.match(text, hint))

        added = make_rule("added", "second-context", ["Second", "Second"], 0.85)
        ruleset.add(added)
        assert result_signature(actual.match("First Second")) == result_signature(
            reference.match("First Second")
        )

        ruleset.rules = ruleset.with_lifecycle_update("live", LifecycleState.RETIRED)
        assert result_signature(actual.match("First Second")) == result_signature(
            reference.match("First Second")
        )

        replacement = make_rule("added", "second-context", ["Replacement"], 0.85)
        ruleset.rules[1] = replacement
        assert result_signature(actual.match("Second Replacement")) == result_signature(
            reference.match("Second Replacement")
        )

    def test_exception_behavior_is_preserved_and_context_filter_stays_lazy(self) -> None:
        bad_rule = make_rule("bad", "bad-context", ["valid", "placeholder"], 0.9)
        ruleset = make_ruleset([bad_rule])
        object.__setattr__(bad_rule, "trigger_keywords", (None, "placeholder"))

        assert Matcher(ruleset).match("anything", "other-context") == []
        with pytest.raises(AttributeError) as expected:
            ReferenceMatcher(ruleset).match("anything")
        with pytest.raises(type(expected.value), match="has no attribute 'lower'"):
            Matcher(ruleset).match("anything")

    def test_get_fired_rules_construction_path_matches_reference(self) -> None:
        ruleset = make_ruleset([
            make_rule("fire-a", "deploy-context", ["Deploy", "deploy"], 0.95),
            make_rule("fire-b", "other-context", ["production"], 0.7, MatchMode.STRICT),
        ])
        expected_results = ReferenceMatcher(ruleset).match("Deploy to production")
        expected = [
            (result.rule, result)
            for result in expected_results
            if result.is_match and result.combined_score >= 0.5
        ]

        actual = get_fired_rules("Deploy to production", ruleset)
        assert [(rule, result_signature([result])) for rule, result in actual] == [
            (rule, result_signature([result])) for rule, result in expected
        ]


class TestFilterHighConfidence:
    def test_filter_threshold(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "test", ["test"], 0.95),
            make_rule("cr-002", "test", ["test"], 0.50),
        ])
        matcher = Matcher(ruleset)
        results = matcher.match("test task")
        filtered = filter_high_confidence(results, threshold=0.7)
        assert len(filtered) == 1
        assert filtered[0].rule.id == "cr-001"


class TestGetFiredRules:
    def test_get_fired_rules(self) -> None:
        ruleset = make_ruleset([
            make_rule("cr-001", "test", ["deploy"], 0.95),
        ])
        fired = get_fired_rules("deploy to production", ruleset)
        assert len(fired) == 1
        rule, result = fired[0]
        assert rule.id == "cr-001"
        assert result.is_match
