"""Hermes adapter for correlation-lib."""

# Deprecated — use CorrelatingMnemosyneProvider instead
from correlation_lib_adapters.hermes.adapter import CorrelationMemoryProvider
from correlation_lib_adapters.hermes.backends import (
    HermesContextBackend,
    HermesRecallBackend,
)
from correlation_lib_adapters.hermes.composition_provider import (
    CorrelatingMnemosyneProvider,
)

__all__ = [
    "HermesRecallBackend",
    "HermesContextBackend",
    "CorrelatingMnemosyneProvider",       # NEW: composition wrapper
    "CorrelationMemoryProvider",           # DEPRECATED: standalone, use above
]
