"""OSRM /table response parsing (offline, requests mocked)."""
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
