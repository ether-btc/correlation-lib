# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-27

### Added
- **`CorrelatingMnemosyneProvider`** — composition wrapper that combines Mnemosyne + correlation-lib into a single `MemoryProvider` registration (`correlation_lib_adapters.hermes.composition_provider`)
- `EffectivenessStore.update_state(rule_id, state)` protocol method — persists lifecycle state transitions
- `EffectivenessStore.log_lifecycle(rule_id, from_state, to_state, reason, triggered_by)` protocol method — audit trail for auto-promote / auto-demote
- `LifecycleManager.last_reason_for(rule_id)` — preserves the manager's specific reason string in the SQLite `lifecycle_log` (the generic engine reason used to overwrite it)
- `CorrelationRule.with_lifecycle(new_state)` — frozen-dataclass-safe lifecycle state setter
- `RuleSet._rebuild_keyword_index()` — explicit keyword index rebuild helper
- `diagnostics.__version__` — pulled from installed package metadata via `importlib.metadata`, no longer hardcoded `"0.1.0"`
- Production starter ruleset at `examples/rules.example.json` — 8-rule starter covering config-change, error-debugging, database-ops, memory-optimization, deployment, security-incident, dependency-update, data-deletion. All rules begin in `testing` state so the auto-promote path graduates them based on real-world effectiveness
- This `CHANGELOG.md` (formalized for v0.2.0 retroactively)

### Changed
- **`Enricher` constructor signature** — now requires `lifecycle_manager: LifecycleManager` as 5th positional arg; this wires `evaluate_lifecycles` into the post-recording path so the engine file no longer needs to mutate state manually
- `HermesRecallBackend` now operates over `BeamMemory` (`mnemosyne.core.beam`) instead of the older `Mnemosyne` API
- `CorrelationMemoryProvider` is now **deprecated**; emits `DeprecationWarning` on import. Use `CorrelatingMnemosyneProvider` instead
- `diagnostics()` output now uses the installed package version instead of a hardcoded string

### Fixed
- **Thread-safety on `evaluate_lifecycles`** — `engine.py` now wraps the capture-from-state / mutate / log-write sequence in a `threading.RLock` so concurrent calls no longer produce corrupted audit trail entries (`audit/cycle-1` finding C1-F1 thread-safety probe)
- **SQLite store hardening** — `tracker.py` `SQLiteEffectivenessStore` rewritten with WAL mode, single shared connection, `threading.Lock`, production pragmas (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000ms`, `foreign_keys=ON`, `cache_size=-64000`, `temp_store=MEMORY`)
- **Lifecycle hard-demote path** — `LifecycleState.VALIDATED` now has a `PROPOSAL` edge so a VALIDATED rule that performs terribly can be sent all the way back to PROPOSAL by the manager's hard-demote path. Without this entry, `LifecycleManager.evaluate()` returned `None` for the hard-demote case (discovered via audit probe C.8)
- **Keyword matching call signature** — `Matcher._match_keywords` now takes keyword-only arguments, eliminating positional-arg ambiguity
- **`object.__setattr__` calls eliminated** — proper setters and `dataclasses.replace` throughout; frozen-dataclass-correct
- **Ruff and type-safety pass** — all E/F/N/W rule categories now clean
- **`default_factory=dict` bug** — fixed in lifecycle log INSERT path
- **Dead-code elif** — removed
- **Encoding bug** in JSON loader — now explicit `utf-8`
- **Hardcoded version string** — replaced with package metadata

### Security
- Hermes adapter gap-analysis fixes — plugin conflict resolution, deprecation warnings, resource leak fix (`CorrelationMemoryProvider` lazy-init + `warnings.warn`)
- `HermesRecallBackend` documented as **read-only** — never writes via `beam.remember`

[0.4.0]: https://github.com/ether-btc/correlation-relevance-plugin/releases/tag/v0.4.0

## [0.3.0] - 2026-07-08

> ⚠️ **Orphan release** — uploaded to PyPI 2026-07-08 without a matching git tag or release notes. Superseded by 0.4.0; no longer recommended. SHA256:
> - `correlation_lib-0.3.0-py3-none-any.whl`: `04b0606bd652fb161006d7260aeb9a7b10eb920b8c1d34d270d5f2af5e932c0e`
> - `correlation_lib-0.3.0.tar.gz`:        `3b9ab4c9fc1783d00a390f357063039dd05e1c5aac6faf1927d23207e654ad1e`

## [0.2.0] - 2026-05-19

### Added
- Core rule-based context enrichment engine (`correlation_lib`)
- Lifecycle state machine: `proposal` → `testing` → `validated` → `promoted` → `retried`, with auto-promote and auto-demote rules
- Keyword / context / confidence matcher with effective-ratio tracking
- SQLite-backed `EffectivenessStore` for self-improvement
- File-based rule provider with optional hot-reload (`watch_enabled: false` default)
- Runtime diagnostics helpers (`correlation_diagnostics`, `dump_diagnostics`)
- Optional Hermes Agent adapter (`correlation_lib_adapters.hermes`) — `HermesRecallBackend`, `HermesContextBackend`, `CorrelationMemoryProvider`
- 61 unit tests across `rules`, `lifecycle`, `matcher`, `tracker`, `enricher` and 7-case end-to-end integration test
- Example rules file (`examples/example_rules.json`) — 4-rule demo
- Production starter rules file (`examples/rules.example.json`) — 8-rule starter covering config-change, error-debugging, database-ops, memory-optimization, deployment, security-incident, dependency-update, data-deletion

### Fixed
- 5-bug patch (`f426e38`): `default_factory` dict, dead elif, encoding, INSERT state, `evaluate_lifecycles` wiring
- Eliminated `object.__setattr__` calls (`0ed8045`): proper setters + `dataclasses.replace`
- `correlation_lib_adapters.hermes` MemoryProvider ABC compliance (`c7fffbf`): added `get_tool_schemas()`
- 3-cycle audit (`e33722a`): static + runtime + integration review — all clear
- `tracker.py` SQLite store: WAL mode, shared connection, thread-safety (`30834f9`)
- Ruff + type-safety pass (`37b271f`)

### Security
- Hermes adapter gap-analysis fixes (`cb309a7`): plugin conflict, deprecation, resource leak

[0.2.0]: https://github.com/ether-btc/correlation-relevance-plugin/releases/tag/v0.2.0
