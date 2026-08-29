"""Repository-wide isolation fixtures for tests that exercise default paths."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def sandbox_hermes_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Prevent default SQLite/Hermes paths from reaching the live user home."""
    sandbox_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("HERMES_HOME", str(sandbox_home))
