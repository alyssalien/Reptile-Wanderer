"""Weather parsing, per-coordinate caching, and offline fallback."""
import weatherHandler as wh


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_parse_and_hot_flag(monkeypatch):
    wh.invalidate_cache()
    monkeypatch.setattr(wh.requests, "get", lambda *a, **k: _Resp(
        {"current": {"temperature_2m": 35, "relative_humidity_2m": 50, "uv_index": 7}}))
    w = wh.get_weather(25.0, 121.5)
    assert w["is_hot"] is True and w["temperature"] == 35 and w["uv_index"] == 7


def test_cache_hits_same_coords(monkeypatch):
    wh.invalidate_cache()
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return _Resp({"current": {"temperature_2m": 20, "relative_humidity_2m": 50, "uv_index": 1}})

    monkeypatch.setattr(wh.requests, "get", fake)
    wh.get_weather(25.0, 121.5)
    wh.get_weather(25.001, 121.501)   # rounds to same grid -> cache hit
    assert calls["n"] == 1


def test_different_coords_separate_entries(monkeypatch):
    wh.invalidate_cache()
    monkeypatch.setattr(wh.requests, "get", lambda *a, **k: _Resp(
        {"current": {"temperature_2m": 20, "relative_humidity_2m": 50, "uv_index": 1}}))
    wh.get_weather(25.0, 121.5)
    wh.get_weather(24.0, 120.0)
    assert (25.0, 121.5) in wh._cache and (24.0, 120.0) in wh._cache


def test_offline_fallback(monkeypatch):
    wh.invalidate_cache()

    def boom(*a, **k):
        raise RuntimeError("net")

    monkeypatch.setattr(wh.requests, "get", boom)
    w = wh.get_weather(10.0, 10.0)
    assert w["is_hot"] is False and w["temperature"] is None and w["uv_index"] == 0
