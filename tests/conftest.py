import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Point every path helper at a throwaway tree."""
    d = tmp_path / "state"
    monkeypatch.setenv("TERM_RESTORE_STATE_DIR", str(d))
    d.mkdir(parents=True)
    return d
