import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vmrc.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_calculations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    request_type TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_snapshots (
    provider TEXT NOT NULL,
    region TEXT NOT NULL,
    instance_type TEXT NOT NULL,
    unit_price REAL NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (provider, region, instance_type)
);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _conn(conn):
    if conn is not None:
        yield conn
    else:
        with closing(_connect()) as c:
            yield c


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db(conn=None):
    with _conn(conn) as c:
        c.executescript(SCHEMA)


def save_scenario(name, request_type, request, result, conn=None):
    with _conn(conn) as c:
        cur = c.execute(
            "INSERT INTO saved_scenarios (name, request_type, request_json, result_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, request_type, json.dumps(request), json.dumps(result), _now()),
        )
        return cur.lastrowid


def get_scenarios(conn=None):
    with _conn(conn) as c:
        rows = c.execute(
            "SELECT id, name, request_type, request_json, result_json, created_at FROM saved_scenarios ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_scenario(scenario_id, conn=None):
    with _conn(conn) as c:
        row = c.execute("SELECT * FROM saved_scenarios WHERE id = ?", (scenario_id,)).fetchone()
        return dict(row) if row else None


def delete_scenario(scenario_id, conn=None):
    with _conn(conn) as c:
        cur = c.execute("DELETE FROM saved_scenarios WHERE id = ?", (scenario_id,))
        return cur.rowcount > 0


# Legacy calculation delegates for backward compatibility
def save_calculation(name, request, result, conn=None):
    return save_scenario(name, "calculation", request, result, conn=conn)


def get_saved_calculations(conn=None):
    return get_scenarios(conn=conn)


def get_calculation(calc_id, conn=None):
    return get_scenario(calc_id, conn=conn)


def delete_calculation(calc_id, conn=None):
    return delete_scenario(calc_id, conn=conn)


def save_price_snapshots(provider, region, prices, source, conn=None):
    with _conn(conn) as c:
        now = _now()
        for instance_type, unit_price in prices.items():
            c.execute(
                "INSERT OR REPLACE INTO price_snapshots"
                " (provider, region, instance_type, unit_price, source, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (provider, region, instance_type, unit_price, source, now),
            )


def get_price_snapshots(provider, region, conn=None):
    with _conn(conn) as c:
        rows = c.execute(
            "SELECT provider, region, instance_type, unit_price, source, fetched_at"
            " FROM price_snapshots WHERE provider = ? AND region = ?",
            (provider, region),
        ).fetchall()
        return [dict(r) for r in rows]
