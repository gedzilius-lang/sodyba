"""Point every test at a throwaway database before the app is imported.

config.py reads SR_DATA_DIR at import time, so this must run first. pytest
imports conftest before any test module, which is what makes it work.
"""
import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="sodyba-test-"))
os.environ["SR_DATA_DIR"] = str(_TMP)

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    from backend.app.db import init_db
    init_db()
