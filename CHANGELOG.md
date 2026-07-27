# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
