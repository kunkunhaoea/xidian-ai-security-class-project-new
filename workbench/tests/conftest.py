"""Offline integration fixtures: no real credentials, DB exports, or paid calls."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "projects" / "project-04-Intelligence Gathering"))


@pytest.fixture(autouse=True)
def no_paid_credentials(monkeypatch):
    import os
    for key in ("AI_MODEL", "AI_BASE_URL"):
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_API_KEY", "")


@pytest.fixture
def store(tmp_path):
    from adapters.shared_files import TopicStore
    value = TopicStore(tmp_path / "shared")
    value.create("topic-a", "课题甲")
    value.create("topic-b", "课题乙")
    return value


@pytest.fixture
def engine(store, tmp_path):
    from adapters.intelligence.engine import Workbench
    return Workbench(store, tmp_path / "state")
