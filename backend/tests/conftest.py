"""Shared pytest fixtures (Step 9).

Every test — new and old — runs against a TEMPORARY per-test SQLite
database: config.DATABASE_URL is pointed at a file inside pytest's
tmp_path and the process-wide engine cache is reset around each test.
The production data/researchflow.db is therefore NEVER touched by the
test suite.
"""

from pathlib import Path

import pytest

from app.core import config
from app.database import database


@pytest.fixture(autouse=True)
def temporary_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Force all persistence in this test into a throwaway SQLite file."""
    db_path = tmp_path / "researchflow-test.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_path}")
    database.reset_engine_for_tests()
    yield db_path
    database.reset_engine_for_tests()  # dispose engine so tmp_path can clean up
