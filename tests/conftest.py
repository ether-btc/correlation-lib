"""Repository-wide isolation fixtures for tests that exercise default paths."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandbox_hermes_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Prevent default SQLite/Hermes paths from reaching the live user home."""
    live_home = Path.home()
    sandbox_home = tmp_path / "hermes-home"
    monkeypatch.setenv("HOME", str(sandbox_home))
    monkeypatch.setenv("USERPROFILE", str(sandbox_home))
    monkeypatch.setenv("HERMES_HOME", str(sandbox_home))

    live_db = (live_home / ".hermes" / "correlation-effectiveness.db").resolve()
    real_connect = sqlite3.connect

    def guarded_connect(database, *args, **kwargs):
        candidate = str(database)
        if candidate.startswith("file:"):
            candidate = candidate[5:].split("?", 1)[0]
        if Path(candidate).expanduser().resolve() == live_db:
            raise AssertionError(f"test attempted to open live effectiveness DB: {live_db}")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
