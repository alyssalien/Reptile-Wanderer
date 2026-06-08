import os
import re
import time
import uuid

from flask import Flask, render_template, request, jsonify, send_file, g

app = Flask(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(_BASE, "static")
MAPS_DIR = os.path.join(STATIC_DIR, "maps")        # per-session map files live here
DEFAULT_MAP = os.path.join(STATIC_DIR, "map.html")  # shared welcome map

# Per-session search context (replaces the old single global). Keyed by session id
# so concurrent users don't overwrite each other's last search / map.
# In-memory: matches the single-gunicorn-worker deployment; survives within a worker.
_SESSION_CTX: dict = {}
_SESSION_TTL = 3600  # drop a session's cached context + map after 1h idle

MAX_STOPS = 5  # cap multi-stop optimisation so route analysis stays fast

_SID_RE = re.compile(r"^[a-f0-9]{32}$")


# ===================== SESSION =====================
@app.before_request
def _ensure_session():
    sid = request.cookies.get("rw_sid", "")
    if not _SID_RE.match(sid):          # missing or malformed (guards path traversal)
        sid = uuid.uuid4().hex
        g._new_sid = sid
    g.sid = sid


@app.after_request
def _set_session_cookie(resp):
    new = getattr(g, "_new_sid", None)
    if new:
        resp.set_cookie("rw_sid", new, max_age=86400, samesite="Lax", httponly=True)
    return resp


def _session_map_path(sid):
    return os.path.join(MAPS_DIR, f"{sid}.html")


def _save_ctx(sid, ctx):
    _SESSION_CTX[sid] = {"data": ctx, "ts": time.time()}
    _prune_sessions()


def _get_ctx(sid):
    entry = _SESSION_CTX.get(sid)
    return entry["data"] if entry else None


def _prune_sessions():
    """Drop idle sessions and delete their orphaned map files."""
    now = time.time()
    expired = [s for s, e in _SESSION_CTX.items() if now - e["ts"] > _SESSION_TTL]
    for s in expired:
        _SESSION_CTX.pop(s, None)
        try:
            os.remove(_session_map_path(s))
        except OSError:
            pass


def _extract_city(display_name: str) -> str:
    """Pull the city/district out of a Nominatim display_name string."""
    for part in display_name.split(","):
        part = part.strip()
        if part.endswith("市") or part.endswith("縣"):
            return part
    return ""


def _generate_default_map():
    if not os.path.exists(DEFAULT_MAP):
        import folium
        os.makedirs(STATIC_DIR, exist_ok=True)
        m = folium.Map(location=[24.7953, 120.9962], zoom_start=15, tiles="OpenStreetMap")
        welcome = (
            '<div style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);'
            'background:rgba(255,255,255,.85);padding:20px;border-radius:12px;'
            'text-align:center;font-size:16px;z-index:9999;">'
            "🦎 Reptile Wanderer<br><small>請在上方輸入地址後按下搜尋</small></div>"
        )
        m.get_root().html.add_child(folium.Element(welcome))
        m.save(DEFAULT_MAP)


@app.route("/")
def index():
    _generate_default_map()
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():
    from apiHandler import geocode, find_nearby, get_route, get_route_table
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

    # 2. Weather first — it's fast (~1s) and independent of the slower Overpass step,
    #    so we can still show the temperature even if finding locations fails below.
    weather = get_weather(origin["lat"], origin["lon"])

    # 3. Find nearby via Overpass (fails over across mirrors).
    #    Auto-widen the radius if the immediate area (1500 m) is sparse.
    from apiHandler import OverpassUnavailable
    widened = False
    try:
        candidates = find_nearby(origin["lat"], origin["lon"], categories)
        if not candidates:
            candidates = find_nearby(origin["lat"], origin["lon"], categories, radius=3000)
            widened = bool(candidates)
    except OverpassUnavailable:
        return jsonify({"error": "地點服務暫時忙碌（OpenStreetMap 伺服器壅塞），請稍候幾秒再試一次。",
                        "weather": weather}), 503
    if not candidates:
        return jsonify({"error": "附近 3 公里內找不到所選類別的地點，請多勾幾個類別、加長步行時間，或換個地址再試。",
                        "weather": weather}), 404

    # 4. Get real walking times in ONE OSRM /table call (was N sequential /route calls).
    #    /table gives time+distance but no geometry — never straight-line distance.
    table = get_route_table(origin["lat"], origin["lon"], candidates)
    for c, t in zip(candidates, table):
        if t:
            c["walk_min"] = t["duration"] / 60
            c["distance_m"] = t["distance"] if t["distance"] is not None else 0
            c["geometry"] = None
        else:
            c["walk_min"] = None
            c["geometry"] = None

    candidates = [c for c in candidates if c["walk_min"] is not None]

    # 5. Community reports
    danger_reports, recommend_reports = get_active_reports()

    # 6. Filter & rank, then keep only the top 20 most suitable
    ranked = filter_and_rank(
        candidates, species, max_walk_min,
        weather["is_hot"], weather.get("uv_index", 0), weather.get("humidity", 60),
        danger_reports, recommend_reports,
    )
    total_matched = len(ranked)
    ranked = ranked[:20]

    # 7. Fetch route geometry only for the #1 destination (table has no shapes)
    top_geometry = None
    if ranked:
        route = get_route(origin["lat"], origin["lon"], ranked[0]["lat"], ranked[0]["lon"])
        if route:
            ranked[0]["geometry"] = route["geometry"]
            top_geometry = route["geometry"]

    # 8. Build this session's Folium map
    build_map(origin, ranked, species, weather, danger_reports, recommend_reports,
              top_geometry, map_path=_session_map_path(g.sid))

    # Cache context for this session (multi-stop /optimize and /report rebuild)
    _save_ctx(g.sid, {
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
            "suitability": r.get("suitability", 0),
            "species_score": round(r.get("species_score", 0), 2),
        }
        for r in ranked
    ]

    return jsonify({
        "results": results,
        "weather": weather,
        "origin": origin,
        "total_found": len(results),
        "total_matched": total_matched,
    })


@app.route("/map")
def serve_map():
    # Serve this session's map if it has searched; otherwise the shared welcome map
    session_map = _session_map_path(g.sid)
    if os.path.exists(session_map):
        return send_file(session_map, mimetype="text/html")
    _generate_default_map()
    if os.path.exists(DEFAULT_MAP):
        return send_file(DEFAULT_MAP, mimetype="text/html")
    return "<p style='font-family:sans-serif;text-align:center;margin-top:40px;'>請先搜尋地點以載入地圖。</p>", 404


@app.route("/optimize", methods=["POST"])
def optimize():
    from routeFilter import optimize_multi_stop
    from apiHandler import get_route
    from mapGenerator import build_map

    ctx = _get_ctx(g.sid)
    if not ctx:
        return jsonify({"error": "請先搜尋地點"}), 400

    data = request.get_json() or {}
    stops = data.get("stops", [])

    if len(stops) < 2:
        return jsonify({"error": "請至少選擇 2 個目的地進行多站點規劃"}), 400

    if len(stops) > MAX_STOPS:
        return jsonify({"error": f"最多只能選擇 {MAX_STOPS} 個目的地，以維持路線分析速度"}), 400

    origin = ctx["origin"]
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
        ranked_candidates=ctx.get("ranked", []),
        species=ctx.get("species", "gecko"),
        weather=ctx.get("weather", {}),
        danger_reports=ctx.get("danger_reports", []),
        recommend_reports=ctx.get("recommend_reports", []),
        top_geometry=None,
        multi_stop_routes=multi_routes,
        multi_stop_ordered=ordered,
        map_path=_session_map_path(g.sid),
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

    ctx = _get_ctx(g.sid)
    data = dict(request.get_json() or {})

    # Geocode location name if no coordinates provided
    if "lat" not in data or "lon" not in data:
        bias_lat = ctx["origin"]["lat"] if ctx else 24.7953
        bias_lon = ctx["origin"]["lon"] if ctx else 120.9962

        location_name = data.get("location_name", "").strip()
        loc = geocode_near(location_name, bias_lat, bias_lon)

        # If not found, retry with city context extracted from last search display_name
        if not loc and ctx:
            city_hint = _extract_city(ctx["origin"].get("display_name", ""))
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

    # Rebuild this session's map so the new marker appears immediately
    if ctx:
        danger_reports, recommend_reports = get_active_reports()
        ranked = ctx.get("ranked", [])
        build_map(
            origin=ctx["origin"],
            ranked_candidates=ranked,
            species=ctx.get("species", "gecko"),
            weather=ctx.get("weather", {}),
            danger_reports=danger_reports,
            recommend_reports=recommend_reports,
            top_geometry=ranked[0]["geometry"] if ranked else None,
            map_path=_session_map_path(g.sid),
        )

    return jsonify({"success": True, "message": msg})


@app.route("/reports")
def list_reports():
    """All currently-active community reports, newest first — feeds the message board."""
    from communityHandler import get_active_reports, purge_old

    purge_old()
    danger, recommend = get_active_reports()

    def _slim(r):
        return {
            "id": r["id"],
            "report_type": r["report_type"],
            "location_name": r.get("location_name", ""),
            "description": r.get("description", ""),
            "timestamp": r.get("timestamp", 0),
            "upvotes": r.get("upvotes", 0),
            "downvotes": r.get("downvotes", 0),
        }

    items = [_slim(r) for r in danger + recommend]
    items.sort(key=lambda r: r["timestamp"], reverse=True)
    return jsonify({"reports": items, "total": len(items)})


@app.route("/reports/clear", methods=["POST"])
def clear_reports():
    """Reset the community board — remove all reports."""
    from communityHandler import clear_all

    ok = clear_all()
    return jsonify({"success": ok})


@app.route("/vote", methods=["POST"])
def vote():
    from communityHandler import vote_report

    data = request.get_json() or {}
    success = vote_report(data.get("report_id", ""), data.get("vote_type", ""))
    return jsonify({"success": success})


if __name__ == "__main__":
    os.makedirs(MAPS_DIR, exist_ok=True)
    os.makedirs(os.path.join(_BASE, "data"), exist_ok=True)
    _generate_default_map()

    # Config via environment so the same image runs locally and in Docker
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5001"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
