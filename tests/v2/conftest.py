"""Test runs must never touch the real runs log: every test config writes runs.csv under a temp dir."""
import tempfile
from pathlib import Path

import pytest

from selection_v2 import config as C

_TMP = Path(tempfile.mkdtemp(prefix="selection_v2_tests_"))
_orig = C.load_config


def _load_config(*a, **kw):
    cfg = _orig(*a, **kw)
    cfg["paths"]["runs_log"] = str(_TMP / "runs.csv")
    return cfg


C.load_config = _load_config            # patched at collection time, before any test module calls load_config()


@pytest.fixture(autouse=True, scope="session")
def _isolate_runs_log():
    yield
