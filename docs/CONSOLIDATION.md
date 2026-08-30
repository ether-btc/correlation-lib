# Correlation Technology Consolidation

Date: 2026-08-29
Status: prepared on `chore/consolidate-python-repos`; runtime activation is intentionally separate.

## Decision

`ether-btc/correlation-lib` is the canonical Python repository.

- `correlation-lib` owns the pure-Python engine, distribution metadata, the newer Hermes composition provider, and performance work.
- `correlation-relevance-plugin` is the historical Hermes audit/integration line. Its valuable audit probes and Hermes integration tests are preserved here; its older duplicate implementation is not merged over the canonical implementation.
- `openclaw-correlation-plugin` remains the upstream/reference implementation for OpenClaw compatibility and schema lineage. It is not merged into the Python package.

## Preserved from the Hermes plugin line

The consolidation retains:

- cycle 1 and cycle 2 audit probes as historical source material;
- Phase B and Phase C audit findings as historical source material;
- Hermes adapter integration behavior through new canonical regression coverage;
- operator-visible legacy adapter initialization health (`is_healthy`, `last_init_error`);
- rule-ID validation and required-field validation;
- lifecycle hard-demotion support, transition reasons, and atomic lifecycle persistence updates.

The old repository's `README.md`, `state.md`, and `CONTINUE_HERE.md` remain historical records in that repository and are not treated as current runtime instructions.

## Runtime boundary

This repository consolidation does not install or activate anything in Hermes. Activation requires a separate, reviewed canary because the live host currently uses native Mnemosyne, has no active correlation plugin/rules file, and is under memory pressure.

Before activation, the canary must prove:

1. the installed package and active plugin are from the same reviewed commit;
2. the real Hermes provider contract loads successfully;
3. enrichment is additive, bounded, observable, and reversible;
4. native Mnemosyne still works if correlation initialization or matching fails;
5. representative task replay shows useful-context rate, false-positive rate, latency, and memory impact.

## Upstream repository disposition

After this branch is reviewed and merged, GitHub cleanup should be performed separately:

- keep `correlation-lib` active as the canonical Python project;
- archive `correlation-relevance-plugin` only after its history, issues, release links, and README redirect are preserved;
- keep `openclaw-correlation-plugin` active as the OpenClaw reference unless its owner-facing purpose changes.

No GitHub repository has been archived, deleted, or redirected by this change.

## Reviewed research disposition

Independent `zai/glm-5.3` research and adversarial review completed 2026-08-29. RepoHunt discovery and source inspection identified `semantica-agi/semantica` as a useful architectural reference for provenance and decision traces, but not a dependency: its current Python metadata declares a heavyweight ML/graph stack including Torch, Transformers, spaCy, SciPy, scikit-learn, sentence-transformers, and graph/vector tooling. The disposition is **REFERENCE_ONLY**; no Semantica code is imported or vendored.

The reviews also identified that the earlier verification suite wrote to the default live effectiveness DB. This was independently reproduced: the un-sandboxed E2E tests created engines without `db_path`, and the direct multi-rule test created `SQLiteEffectivenessStore()` without a path. The current suite now has a repository-wide temporary `HOME`/`HERMES_HOME` fixture and dynamic default-path resolution in `tracker.py`.

Verification after isolation repair:

- `python3 -m pytest tests/ -q` — 68 passed, 1 warning, 0 failed;
- normal-process `strace` — 0 opens of `/home/hermes-pi/.hermes/correlation-effectiveness.db`;
- live DB lifecycle row count — 102 before and after the traced run;
- `python3 -m ruff check correlation_lib correlation_lib_adapters tests` — passed;
- `git diff --check` — passed;
- `uv build --wheel --out-dir /tmp/correlation-lib-build` — built `correlation_lib-0.4.0-py3-none-any.whl`.

The existing live DB is retained untouched and labeled contaminated historical evidence; cleanup requires a separate approved operation.
