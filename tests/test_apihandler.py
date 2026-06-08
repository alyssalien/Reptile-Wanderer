"""OSRM /table parsing and Overpass mirror fallback (offline, requests mocked)."""
import pytest

import apiHandler as api


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_table_parses_durations_and_distances(monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp(
        {"code": "Ok", "durations": [[0, 600, 300]], "distances": [[0, 800, 400]]}))
    out = api.get_route_table(24.8, 121.0, [{"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}])
    assert out == [{"duration": 600, "distance": 800},
                   {"duration": 300, "distance": 400}]


def test_table_empty_destinations():
    assert api.get_route_table(24.8, 121.0, []) == []


def test_table_osrm_error_returns_nones(monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp({"code": "NoRoute"}))
    out = api.get_route_table(24.8, 121.0, [{"lat": 1, "lon": 1}])
    assert out == [None]


def test_table_missing_distance_annotation(monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp(
        {"code": "Ok", "durations": [[0, 600]]}))
    out = api.get_route_table(24.8, 121.0, [{"lat": 1, "lon": 1}])
    assert out == [{"duration": 600, "distance": None}]


def test_table_network_exception_returns_nones(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(api.requests, "get", boom)
    out = api.get_route_table(24.8, 121.0, [{"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}])
    assert out == [None, None]


# --- Overpass mirror fallback -------------------------------------------------
class _OverpassResp:
    def __init__(self, ok):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("HTTP 504")

    def json(self):
        return {"elements": [{"type": "node", "lat": 24.8, "lon": 121.0,
                              "tags": {"name": "中央公園", "leisure": "park"}}]}


def test_find_nearby_fails_over_to_next_mirror(monkeypatch):
    calls = {"n": 0}

    def post(url, **k):
        calls["n"] += 1
        return _OverpassResp(calls["n"] >= 2)   # first mirror 504s, second succeeds

    monkeypatch.setattr(api.requests, "post", post)
    out = api.find_nearby(24.8, 121.0, ["park"])
    assert calls["n"] == 2
    assert len(out) == 1 and out[0]["category"] == "park"


def test_find_nearby_raises_when_all_mirrors_fail(monkeypatch):
    def post(url, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(api.requests, "post", post)
    with pytest.raises(api.OverpassUnavailable):
        api.find_nearby(24.8, 121.0, ["park"])


def test_find_nearby_no_categories_returns_empty():
    assert api.find_nearby(24.8, 121.0, []) == []
