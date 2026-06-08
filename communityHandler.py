import json
import os
import sqlite3
import time
import uuid

_BASE = os.path.dirname(os.path.abspath(__file__))
# DB path is env-overridable so tests can point at a throwaway database
DB_PATH = os.environ.get("RW_DB_PATH", os.path.join(_BASE, "data", "reports.db"))
LEGACY_JSON = os.path.join(_BASE, "data", "reports.json")

REPORT_TTL = 3 * 3600       # 3 hours default lifetime
UPVOTE_THRESHOLD = 5        # upvotes needed to extend lifetime
DOWNVOTE_THRESHOLD = 3      # downvotes needed for early removal
EXTEND_DURATION = 3600      # extra 1 hour per upvote extension

# A report is "active" while within its (possibly extended) TTL and not downvoted out.
# Note: upvotes/:upv is INTEGER division in SQLite — matches the old // logic.
_ACTIVE_CLAUSE = (
    "(:now - timestamp) <= (:ttl + (upvotes / :upv) * :ext) "
    "AND downvotes < :down"
)


def _active_params():
    return {
        "now": time.time(),
        "ttl": REPORT_TTL,
        "upv": UPVOTE_THRESHOLD,
        "ext": EXTEND_DURATION,
        "down": DOWNVOTE_THRESHOLD,
    }


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the table if needed and migrate any legacy reports.json once."""
    conn = _connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reports (
                id            TEXT PRIMARY KEY,
                location_name TEXT,
                report_type   TEXT,
                description   TEXT,
                lat           REAL,
                lon           REAL,
                timestamp     REAL,
                upvotes       INTEGER DEFAULT 0,
                downvotes     INTEGER DEFAULT 0
            )"""
        )
        conn.execute("PRAGMA journal_mode=WAL")

        # One-time migration: import legacy JSON if the table is still empty
        empty = conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"] == 0
        if empty and os.path.exists(LEGACY_JSON):
            try:
                with open(LEGACY_JSON, "r", encoding="utf-8") as f:
                    for r in json.load(f):
                        conn.execute(
                            "INSERT OR IGNORE INTO reports VALUES "
                            "(:id,:location_name,:report_type,:description,:lat,:lon,:timestamp,:upvotes,:downvotes)",
                            {
                                "id": r.get("id") or uuid.uuid4().hex,
                                "location_name": str(r.get("location_name", ""))[:100],
                                "report_type": r.get("report_type", "danger"),
                                "description": str(r.get("description", ""))[:300],
                                "lat": float(r.get("lat", 0)),
                                "lon": float(r.get("lon", 0)),
                                "timestamp": float(r.get("timestamp", 0)),
                                "upvotes": int(r.get("upvotes", 0)),
                                "downvotes": int(r.get("downvotes", 0)),
                            },
                        )
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()


def add_report(data):
    try:
        report = {
            "id": str(uuid.uuid4()),
            "location_name": str(data.get("location_name", "未知地點"))[:100],
            "report_type": data.get("report_type", "danger"),
            "description": str(data.get("description", ""))[:300],
            "lat": float(data.get("lat", 24.7953)),
            "lon": float(data.get("lon", 120.9962)),
            "timestamp": time.time(),
            "upvotes": 0,
            "downvotes": 0,
        }
    except (TypeError, ValueError):
        return False

    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO reports VALUES "
            "(:id,:location_name,:report_type,:description,:lat,:lon,:timestamp,:upvotes,:downvotes)",
            report,
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_active_reports():
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM reports WHERE {_ACTIVE_CLAUSE}", _active_params()
        ).fetchall()
    except Exception:
        return [], []
    finally:
        conn.close()

    danger, recommend = [], []
    for row in rows:
        r = dict(row)
        if r["report_type"] == "danger":
            danger.append(r)
        elif r["report_type"] == "recommend":
            recommend.append(r)
    return danger, recommend


def vote_report(report_id, vote_type):
    if vote_type not in ("up", "down"):
        return False
    column = "upvotes" if vote_type == "up" else "downvotes"

    conn = _connect()
    try:
        cur = conn.execute(
            f"UPDATE reports SET {column} = {column} + 1 WHERE id = ?", (report_id,)
        )
        conn.commit()
        return cur.rowcount > 0   # False when report_id doesn't exist
    except Exception:
        return False
    finally:
        conn.close()


def clear_all():
    """Delete every report — resets the community board to empty."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM reports")
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def purge_old():
    conn = _connect()
    try:
        conn.execute(
            f"DELETE FROM reports WHERE NOT ({_ACTIVE_CLAUSE})", _active_params()
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# Initialise on import so the app and tests can use it immediately
init_db()
