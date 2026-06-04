import os
import time
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)

# Stores last search result so /optimize can rebuild the map without re-searching
_last_ctx: dict = {}


def _extract_city(display_name: str) -> str:
    """Pull the city/district out of a Nominatim display_name string."""
    for part in display_name.split(","):
        part = part.strip()
        if part.endswith("市") or part.endswith("縣"):
            return part
    return ""


def _generate_default_map():
    map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "map.html")
    if not os.path.exists(map_path):
        import folium
        os.makedirs(os.path.dirname(map_path), exist_ok=True)
        m = folium.Map(location=[24.7953, 120.9962], zoom_start=15, tiles="OpenStreetMap")
        welcome = (
            '<div style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);"'
            'style="background:rgba(255,255,255,.85);padding:20px;border-radius:12px;'
            'text-align:center;font-size:16px;z-index:9999;">'
            "🦎 Reptile Wanderer<br><small>請在上方輸入地址後按下搜尋</small></div>"
        )
        m.get_root().html.add_child(folium.Element(welcome))
        m.save(map_path)


@app.route("/")
def index():
    _generate_default_map()
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():
    from apiHandler import geocode, find_nearby, get_route
    from routeFilter import filter_and_rank
    from mapGenerator import build_map
    from weatherHandler import get_weather
    from communityHandler import get_active_reports, purge_old

    purge_old()

    data = request.get_json() or {}
    address = data.get("address", "").strip()
    species = data.get("species", "gecko")
    max_walk_min = int(data.get("max_walk_min", 15))
    categories = data.get("categories") or ["park"]

    if not address:
        return jsonify({"error": "請輸入起點地址"}), 400

    # 1. Geocode
    origin = geocode(address)
    if not origin:
        return jsonify({"error": f"找不到「{address}」，請嘗試更具體的地址或加上城市名稱"}), 400

    # 2. Find nearby via Overpass
    candidates = find_nearby(origin["lat"], origin["lon"], categories)
    if not candidates:
        return jsonify({"error": "附近找不到符合條件的地點，請更換類別或搜尋不同地址"}), 404

    # 3. Get real walking routes (OSRM) — never use straight-line distance
    for c in candidates:
        route = get_route(origin["lat"], origin["lon"], c["lat"], c["lon"])
        if route:
            c["walk_min"] = route["duration"] / 60
            c["distance_m"] = route["distance"]
            c["geometry"] = route["geometry"]
        else:
            c["walk_min"] = None
            c["geometry"] = None

    candidates = [c for c in candidates if c["walk_min"] is not None]

    # 4. Weather
    weather = get_weather()

    # 5. Community reports
    danger_reports, recommend_reports = get_active_reports()

    # 6. Filter & rank
    ranked = filter_and_rank(
        candidates, species, max_walk_min,
        weather["is_hot"], weather.get("uv_index", 0), weather.get("humidity", 60),
        danger_reports, recommend_reports,
    )

    # 7. Build Folium map
    top_geometry = ranked[0]["geometry"] if ranked else None
    build_map(origin, ranked, species, weather, danger_reports, recommend_reports, top_geometry)

    # Cache context for multi-stop /optimize
    _last_ctx.update({
        "origin": origin,
        "ranked": ranked,
        "species": species,
        "weather": weather,
        "danger_reports": danger_reports,
        "recommend_reports": recommend_reports,
    })

    results = [
        {
            "name": r["name"],
            "category": r["category"],
            "walk_min": round(r["walk_min"], 1),
            "distance_m": round(r.get("distance_m", 0)),
            "lat": r["lat"],
            "lon": r["lon"],
            "is_shaded": r.get("is_shaded", False),
            "rating": r.get("rating", 0),
            "species_score": round(r.get("species_score", 0), 2),
        }
        for r in ranked
    ]

    return jsonify({
        "results": results,
        "weather": weather,
        "origin": origin,
        "total_found": len(results),
    })


@app.route("/map")
def serve_map():
    map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "map.html")
    if os.path.exists(map_path):
        return send_file(map_path, mimetype="text/html")
    return "<p style='font-family:sans-serif;text-align:center;margin-top:40px;'>請先搜尋地點以載入地圖。</p>", 404


@app.route("/optimize", methods=["POST"])
def optimize():
    from routeFilter import optimize_multi_stop
    from apiHandler import get_route
    from mapGenerator import build_map

    if not _last_ctx:
        return jsonify({"error": "請先搜尋地點"}), 400

    data = request.get_json() or {}
    stops = data.get("stops", [])

    if len(stops) < 2:
        return jsonify({"error": "請至少選擇 2 個目的地進行多站點規劃"}), 400

    origin = _last_ctx["origin"]
    ordered = optimize_multi_stop(origin["lat"], origin["lon"], stops)

    multi_routes = []
    total_time = 0.0
    prev_lat, prev_lon = origin["lat"], origin["lon"]

    for stop in ordered:
        route = get_route(prev_lat, prev_lon, stop["lat"], stop["lon"])
        if route:
            multi_routes.append(route["geometry"])
            total_time += route["duration"] / 60
        else:
            multi_routes.append(None)
        prev_lat, prev_lon = stop["lat"], stop["lon"]

    build_map(
        origin=origin,
        ranked_candidates=_last_ctx.get("ranked", []),
        species=_last_ctx.get("species", "gecko"),
        weather=_last_ctx.get("weather", {}),
        danger_reports=_last_ctx.get("danger_reports", []),
        recommend_reports=_last_ctx.get("recommend_reports", []),
        top_geometry=None,
        multi_stop_routes=multi_routes,
        multi_stop_ordered=ordered,
    )

    return jsonify({
        "ordered_stops": [
            {"name": s["name"], "walk_min": round(s.get("walk_min", 0), 1)}
            for s in ordered
        ],
        "total_walk_min": round(total_time, 1),
    })


@app.route("/report", methods=["POST"])
def submit_report():
    from communityHandler import add_report, get_active_reports
    from apiHandler import geocode_near
    from mapGenerator import build_map

    data = dict(request.get_json() or {})

    # Geocode location name if no coordinates provided
    if "lat" not in data or "lon" not in data:
        bias_lat = _last_ctx["origin"]["lat"] if _last_ctx else 24.7953
        bias_lon = _last_ctx["origin"]["lon"] if _last_ctx else 120.9962

        location_name = data.get("location_name", "").strip()
        loc = geocode_near(location_name, bias_lat, bias_lon)

        # If not found, retry with city context extracted from last search display_name
        if not loc and _last_ctx:
            city_hint = _extract_city(_last_ctx["origin"].get("display_name", ""))
            if city_hint and city_hint not in location_name:
                loc = geocode_near(f"{location_name} {city_hint}", bias_lat, bias_lon)

        if loc:
            data["lat"] = loc["lat"]
            data["lon"] = loc["lon"]
            data["_geocoded"] = True
        else:
            # Place marker at last search origin so it appears on the correct map
            data["lat"] = bias_lat
            data["lon"] = bias_lon
            data["_geocoded"] = False

    geocoded_ok = data.pop("_geocoded", True)
    success = add_report(data)
    if not success:
        return jsonify({"success": False, "message": "❌ 回報失敗，請稍後再試。"}), 500

    if not geocoded_ok:
        msg = "✅ 回報成功（找不到精確座標，標記已放在搜尋起點附近）。建議地點名稱加上城市，例如「成功湖 新竹」。"
    else:
        msg = "✅ 回報成功！感謝您的貢獻，地圖即將更新。"

    # Rebuild map so new marker appears immediately
    if _last_ctx:
        danger_reports, recommend_reports = get_active_reports()
        ranked = _last_ctx.get("ranked", [])
        build_map(
            origin=_last_ctx["origin"],
            ranked_candidates=ranked,
            species=_last_ctx.get("species", "gecko"),
            weather=_last_ctx.get("weather", {}),
            danger_reports=danger_reports,
            recommend_reports=recommend_reports,
            top_geometry=ranked[0]["geometry"] if ranked else None,
        )

    return jsonify({"success": True, "message": msg})


@app.route("/vote", methods=["POST"])
def vote():
    from communityHandler import vote_report

    data = request.get_json() or {}
    success = vote_report(data.get("report_id", ""), data.get("vote_type", ""))
    return jsonify({"success": success})


if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    os.makedirs("data", exist_ok=True)
    _generate_default_map()
    app.run(debug=True, port=5001)
