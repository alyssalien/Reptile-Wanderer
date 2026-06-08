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


@pytest.fixture(autouse=True)
def _isolate_overpass_cache(tmp_path, monkeypatch):
    # Fresh in-memory + disk cache per test (no leakage, no real file writes)
    monkeypatch.setattr(api, "_overpass_cache", {})
    monkeypatch.setattr(api, "OVERPASS_CACHE_FILE", str(tmp_path / "overpass_cache.json"))
    yield


def test_find_nearby_survives_a_failing_mirror(monkeypatch):
    # Mirrors race in parallel; one failing must not break the search.
    def post(url, **k):
        return _OverpassResp(url != api.OVERPASS_URLS[0])   # first mirror 504s, others OK

    monkeypatch.setattr(api.requests, "post", post)
    out = api.find_nearby(24.8, 121.0, ["park"])
    assert len(out) == 1 and out[0]["category"] == "park"


def test_find_nearby_caches_repeat_search(monkeypatch):
    calls = {"n": 0}

    def post(url, **k):
        calls["n"] += 1
        return _OverpassResp(True)

    monkeypatch.setattr(api.requests, "post", post)
    api.find_nearby(24.8, 121.0, ["park"])
    first = calls["n"]
    api.find_nearby(24.8, 121.0, ["park"])   # identical search -> served from cache
    assert calls["n"] == first               # no extra network calls


def test_find_nearby_raises_when_all_mirrors_fail(monkeypatch):
    def post(url, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(api.requests, "post", post)
    with pytest.raises(api.OverpassUnavailable):
        api.find_nearby(24.8, 121.0, ["park"])


def test_find_nearby_serves_stale_cache_when_mirrors_down(monkeypatch):
    # 1) First search succeeds and is cached
    monkeypatch.setattr(api.requests, "post", lambda url, **k: _OverpassResp(True))
    first = api.find_nearby(24.8, 121.0, ["park"])
    assert len(first) == 1

    # 2) Force the cached entry to look expired, then make all mirrors fail
    key = next(iter(api._overpass_cache))
    api._overpass_cache[key]["ts"] = 0
    monkeypatch.setattr(api.requests, "post",
                        lambda url, **k: (_ for _ in ()).throw(RuntimeError("504")))

    # 3) Still returns the stale copy instead of erroring
    out = api.find_nearby(24.8, 121.0, ["park"])
    assert len(out) == 1 and out[0]["category"] == "park"


def test_find_nearby_no_categories_returns_empty():
    assert api.find_nearby(24.8, 121.0, []) == []


class _RemarkResp:
    """Overpass HTTP 200 but server-side timeout: empty elements + a remark."""
    def raise_for_status(self):
        pass

    def json(self):
        return {"elements": [], "remark": "runtime error: Query timed out"}


def test_remark_timeout_treated_as_failure(monkeypatch):
    monkeypatch.setattr(api.requests, "post", lambda url, **k: _RemarkResp())
    with pytest.raises(api.OverpassUnavailable):
        api.find_nearby(24.8, 121.0, ["park"])


class _EmptyResp:
    """Genuine empty result (200, no remark) — valid 'nothing nearby'."""
    def raise_for_status(self):
        pass

    def json(self):
        return {"elements": []}


def test_genuine_empty_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def post(url, **k):
        calls["n"] += 1
        return _EmptyResp()

    monkeypatch.setattr(api.requests, "post", post)
    assert api.find_nearby(24.8, 121.0, ["park"]) == []
    after_first = calls["n"]
    assert api.find_nearby(24.8, 121.0, ["park"]) == []   # re-queries, not cached
    assert calls["n"] > after_first
