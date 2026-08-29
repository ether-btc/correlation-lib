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

- cycle 1 and cycle 2 audit probes;
- Phase B and Phase C audit probes;
- Hermes adapter integration fixtures/tests;
- the provider failure and lifecycle regression coverage represented by those tests.

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
