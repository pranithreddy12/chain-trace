import os
import tempfile
from pathlib import Path

import pytest

# Point every test at a throwaway SQLite file so runs never touch the dev DB
# and never leak cached transfers between tests.
_TMP_DB = Path(tempfile.gettempdir()) / "chaintrace_test.db"
os.environ["DATABASE_PATH"] = str(_TMP_DB)


@pytest.fixture(autouse=True)
def _fresh_db():
    import src.config.settings as settings_mod
    import src.persistence.database as db_mod

    if _TMP_DB.exists():
        _TMP_DB.unlink()
    settings_mod._settings = None
    db_mod._db = None
    yield
    settings_mod._settings = None
    db_mod._db = None
