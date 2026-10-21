import os
import sqlite3
import sys
import tempfile

import httpx
import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import database

# Isolate every test run from the developer's real vmrc.db.
_TMP_DIR = tempfile.mkdtemp(prefix="vmrc-test-")
database.DB_PATH = os.path.join(_TMP_DIR, "test.db")
database.init_db()

from main import app  # noqa: E402


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def clean_saved_tables():
    conn = sqlite3.connect(database.DB_PATH, isolation_level=None)
    conn.execute("DELETE FROM saved_calculations")
    conn.execute("DELETE FROM saved_scenarios")
    conn.close()
    yield
