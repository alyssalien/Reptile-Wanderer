"""Flask routes with all network + map rendering mocked."""
import os

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app
    import apiHandler
    import weatherHandler
    import mapGenerator
    import communityHandler as ch

    # Isolated DB (no legacy migration) and a temp per-session map dir
    monkeypatch.setattr(ch, "DB_PATH", str(tmp_path / "reports.db"))
    monkeypatch.setattr(ch, "LEGACY_JSON", str(tmp_path / "none.json"))
    ch.init_db()
    monkeypatch.setattr(app, "MAPS_DIR", str(tmp_path / "maps"))
    os.makedirs(tmp_path / "maps", exist_ok=True)

    # Stub all outbound network + the (folium) map build
    monkeypatch.setattr(apiHandler, "geocode",
                        lambda a: {"lat": 24.8, "lon": 121.0,
                                   "display_name": "清華大學, 東區, 新竹市, 台灣"})
    monkeypatch.setattr(apiHandler, "find_nearby",
                        lambda lat, lon, cats, **k: [{"name": "P", "lat": 24.801, "lon": 121.001,
                                                      "category": "park", "is_shaded": True, "rating": 0}])
    monkeypatch.setattr(apiHandler, "get_route_table",
                        lambda lat, lon, dests: [{"duration": 300, "distance": 400} for _ in dests])
    monkeypatch.setattr(apiHandler, "get_route",
                        lambda *a: {"duration": 300, "distance": 400,
                                    "geometry": {"type": "LineString",
                                                 "coordinates": [[121.0, 24.8], [121.001, 24.801]]}})
    monkeypatch.setattr(weatherHandler, "get_weather",
                        lambda lat=0, lon=0: {"temperature": 28, "humidity": 60,
                                              "uv_index": 3, "is_hot": False})
    monkeypatch.setattr(mapGenerator, "build_map", lambda *a, **k: None)

    app.app.config["TESTING"] = True
    return app.app.test_client()


def test_index_ok(client):
    assert client.get("/").status_code == 200


def test_search_happy_path_sets_session(client):
    r = client.post("/search", json={"address": "清華大學", "species": "gecko",
                                     "max_walk_min": 15, "categories": ["park"]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["total_found"] == 1
    assert body["results"][0]["name"] == "P"
    # A session cookie is issued on first visit
    assert "rw_sid" in r.headers.get("Set-Cookie", "")


def test_search_requires_address(client):
    r = client.post("/search", json={"address": "   "})
    assert r.status_code == 400


def test_optimize_requires_prior_search(client):
    r = client.post("/optimize", json={"stops": [{"lat": 1, "lon": 1}, {"lat": 2, "lon": 2}]})
    assert r.status_code == 400


def test_search_then_optimize(client):
    client.post("/search", json={"address": "清華大學", "species": "gecko",
                                 "max_walk_min": 15, "categories": ["park"]})
    r = client.post("/optimize", json={"stops": [
        {"name": "A", "lat": 24.801, "lon": 121.001, "walk_min": 5},
        {"name": "B", "lat": 24.802, "lon": 121.002, "walk_min": 6},
    ]})
    assert r.status_code == 200
    assert "ordered_stops" in r.get_json()


def test_vote_unknown_report(client):
    r = client.post("/vote", json={"report_id": "nope", "vote_type": "up"})
    assert r.get_json()["success"] is False
