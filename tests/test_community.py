"""SQLite community-report store: CRUD, voting, TTL expiry, migration."""
import json
import time

import pytest


@pytest.fixture
def ch(tmp_path, monkeypatch):
    import communityHandler as mod
    # Point at a throwaway DB and disable legacy-JSON migration for a clean slate
    monkeypatch.setattr(mod, "DB_PATH", str(tmp_path / "reports.db"))
    monkeypatch.setattr(mod, "LEGACY_JSON", str(tmp_path / "none.json"))
    mod.init_db()
    return mod


def _add(ch, **over):
    base = {"location_name": "L", "report_type": "danger",
            "description": "d", "lat": 24.8, "lon": 121.0}
    base.update(over)
    return ch.add_report(base)


def test_add_and_get(ch):
    assert _add(ch, location_name="A") is True
    danger, recommend = ch.get_active_reports()
    assert len(danger) == 1 and recommend == []
    assert danger[0]["location_name"] == "A"


def test_recommend_bucket(ch):
    _add(ch, report_type="recommend")
    danger, recommend = ch.get_active_reports()
    assert danger == [] and len(recommend) == 1


def test_vote_unknown_id_returns_false(ch):
    assert ch.vote_report("does-not-exist", "up") is False


def test_invalid_vote_type_returns_false(ch):
    assert ch.vote_report("any", "sideways") is False


def test_vote_increments(ch):
    _add(ch)
    rid = ch.get_active_reports()[0][0]["id"]
    assert ch.vote_report(rid, "up") is True
    assert ch.get_active_reports()[0][0]["upvotes"] == 1


def test_expired_report_inactive_and_purged(ch):
    _add(ch, location_name="old")
    conn = ch._connect()
    conn.execute("UPDATE reports SET timestamp = ?", (time.time() - ch.REPORT_TTL - 10,))
    conn.commit()
    conn.close()

    assert ch.get_active_reports() == ([], [])
    ch.purge_old()
    conn = ch._connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
    conn.close()
    assert n == 0


def test_downvotes_remove_report(ch):
    _add(ch)
    rid = ch.get_active_reports()[0][0]["id"]
    for _ in range(ch.DOWNVOTE_THRESHOLD):
        ch.vote_report(rid, "down")
    assert ch.get_active_reports() == ([], [])


def test_upvotes_extend_ttl(ch):
    _add(ch)
    rid = ch.get_active_reports()[0][0]["id"]
    # Age past base TTL but within one upvote-extension window
    conn = ch._connect()
    conn.execute("UPDATE reports SET timestamp = ?, upvotes = ? WHERE id = ?",
                 (time.time() - ch.REPORT_TTL - 100, ch.UPVOTE_THRESHOLD, rid))
    conn.commit()
    conn.close()
    danger, _ = ch.get_active_reports()
    assert len(danger) == 1   # extension keeps it alive


def test_clear_all_empties_board(ch):
    _add(ch, location_name="A")
    _add(ch, location_name="B", report_type="recommend")
    assert ch.clear_all() is True
    assert ch.get_active_reports() == ([], [])


def test_legacy_json_migration(tmp_path, monkeypatch):
    import communityHandler as mod
    legacy = tmp_path / "reports.json"
    legacy.write_text(json.dumps([{
        "id": "abc", "location_name": "舊回報", "report_type": "danger",
        "description": "有狗", "lat": 24.79, "lon": 120.99,
        "timestamp": time.time(), "upvotes": 0, "downvotes": 0,
    }]), encoding="utf-8")
    monkeypatch.setattr(mod, "DB_PATH", str(tmp_path / "reports.db"))
    monkeypatch.setattr(mod, "LEGACY_JSON", str(legacy))
    mod.init_db()
    danger, _ = mod.get_active_reports()
    assert len(danger) == 1 and danger[0]["id"] == "abc"
