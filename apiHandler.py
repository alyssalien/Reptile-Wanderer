import requests
import time

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Multiple Overpass mirrors — the public ones overload often (HTTP 504). We try
# them in order and fail over, so one busy server no longer breaks every search.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OSRM_BASE = "http://router.project-osrm.org"
OSRM_ROUTE_URL = f"{OSRM_BASE}/route/v1/foot"
OSRM_TABLE_URL = f"{OSRM_BASE}/table/v1/foot"
HEADERS = {
    "User-Agent": "ReptileWanderer/1.0 (student project; contact: student@nthu.edu.tw)",
    "Accept": "application/json, */*",
}

_geocode_cache = {}

# Maps UI category names to OSM tag pairs
CATEGORY_TAGS = {
    "park":        [("leisure", "park"), ("leisure", "garden")],
    "grass":       [("landuse", "grass"), ("leisure", "pitch"), ("landuse", "meadow")],
    "convenience": [("shop", "convenience")],
    "forest":      [("landuse", "forest"), ("natural", "wood"), ("leisure", "nature_reserve")],
    "campus":      [("amenity", "university"), ("amenity", "college")],
}

# OSM tags that imply meaningful shade coverage (for gecko heat-avoidance logic)
SHADED_TAG_VALUES = {
    "leisure": {"park", "garden", "nature_reserve"},
    "landuse": {"forest"},
    "natural": {"wood"},
}


def geocode_near(address, bias_lat, bias_lon, radius_deg=0.1):
    """Geocode with a viewbox centred on (bias_lat, bias_lon) to avoid wrong-country results."""
    # Build a bounding box ±radius_deg degrees around the bias point
    viewbox = (
        f"{bias_lon - radius_deg},{bias_lat + radius_deg},"
        f"{bias_lon + radius_deg},{bias_lat - radius_deg}"
    )
    cache_key = f"{address}@{bias_lat:.3f},{bias_lon:.3f}"
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    time.sleep(1)
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": "tw",
        "viewbox": viewbox,
        "bounded": 0,   # 0 = prefer results in viewbox but fall back outside if nothing found
    }
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        r = results[0]
        result = {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "display_name": r.get("display_name", address),
        }
        _geocode_cache[cache_key] = result
        return result
    except Exception:
        return None


def geocode(address):
    if address in _geocode_cache:
        return _geocode_cache[address]

    time.sleep(1)  # Nominatim public API: max 1 req/sec
    # countrycodes=tw keeps results in Taiwan — otherwise "清華大學" matches
    # the more prominent Tsinghua University in Beijing instead of NTHU in Hsinchu.
    params = {"q": address, "format": "json", "limit": 1,
              "addressdetails": 1, "countrycodes": "tw"}
    try:
        resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        r = results[0]
        result = {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "display_name": r.get("display_name", address),
        }
        _geocode_cache[address] = result
        return result
    except Exception:
        return None


class OverpassUnavailable(Exception):
    """Raised when every Overpass mirror fails — distinct from 'genuinely no results'."""


# Short-lived cache of successful Overpass responses, so repeating the same
# search (or retrying after a hiccup) is instant instead of hitting the network.
_overpass_cache = {}
OVERPASS_CACHE_TTL = 600  # 10 minutes


def _overpass_query_one(url, query):
    # (connect, read): drop an unreachable mirror fast, allow a slow query up to 25 s
    resp = requests.post(url, data={"data": query}, headers=HEADERS, timeout=(5, 25))
    resp.raise_for_status()
    return resp.json()


def find_nearby(lat, lon, categories, radius=1500):
    conditions = []
    for cat in categories:
        for key, val in CATEGORY_TAGS.get(cat, []):
            conditions.append(f'node["{key}"="{val}"](around:{radius},{lat},{lon});')
            conditions.append(f'way["{key}"="{val}"](around:{radius},{lat},{lon});')

    if not conditions:
        return []

    # Serve a recent identical search from cache
    cache_key = (round(lat, 3), round(lon, 3), tuple(sorted(categories)), radius)
    cached = _overpass_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < OVERPASS_CACHE_TTL:
        data = cached["data"]
    else:
        # Lower server-side timeout so a busy mirror gives up sooner
        query = f"[out:json][timeout:25];({''.join(conditions)});out center 60;"

        # Race all mirrors AT ONCE — the first healthy one wins, so we wait for the
        # fastest server rather than timing each out one-by-one.
        import concurrent.futures
        data = None
        last_error = None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(OVERPASS_URLS))
        try:
            futures = [executor.submit(_overpass_query_one, url, query) for url in OVERPASS_URLS]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    data = fut.result()
                    break  # first success wins; ignore the slower mirrors
                except Exception as e:
                    last_error = e
        finally:
            executor.shutdown(wait=False)  # don't block on the losing requests

        if data is None:
            raise OverpassUnavailable(f"all Overpass mirrors failed: {last_error}")

        _overpass_cache[cache_key] = {"data": data, "ts": time.time()}

    candidates = []
    seen = set()

    CATEGORY_FALLBACK_NAMES = {
        "park": "公園", "grass": "草地 / 球場", "forest": "森林 / 自然保護區",
        "convenience": "便利商店", "campus": "校園",
    }

    for elem in data.get("elements", []):
        tags = elem.get("tags", {})
        name = tags.get("name", tags.get("name:zh", "")).strip()

        # Determine category first so we can build a fallback name
        cat = "park"
        for cat_name, tag_pairs in CATEGORY_TAGS.items():
            if any(tags.get(k) == v for k, v in tag_pairs):
                cat = cat_name
                break

        if not name:
            name = tags.get("name:en", "").strip() or CATEGORY_FALLBACK_NAMES.get(cat, "地點")

        if elem["type"] == "node":
            c_lat, c_lon = elem["lat"], elem["lon"]
        else:
            center = elem.get("center", {})
            c_lat = center.get("lat")
            c_lon = center.get("lon")
            if not c_lat:
                continue

        key_id = f"{c_lat:.4f},{c_lon:.4f}"
        if key_id in seen:
            continue
        seen.add(key_id)

        is_shaded = any(
            tags.get(tag_key) in tag_vals
            for tag_key, tag_vals in SHADED_TAG_VALUES.items()
        )

        # OSM stars/rating field as proxy score; 0 if absent
        raw_rating = tags.get("stars", tags.get("rating", 0))
        try:
            rating = float(raw_rating) if raw_rating else 0.0
        except (TypeError, ValueError):
            rating = 0.0

        candidates.append({
            "name": name,
            "lat": c_lat,
            "lon": c_lon,
            "category": cat,
            "is_shaded": is_shaded,
            "rating": rating,
            "osm_tags": tags,
        })

    return candidates


def get_route_table(origin_lat, origin_lon, destinations):
    """One OSRM /table call for walking time+distance from origin to every destination.

    Replaces N sequential get_route() calls. Returns a list aligned with
    `destinations`; each item is {"duration": sec, "distance": m} or None if
    that destination is unreachable. NOTE: /table returns times only — no route
    geometry — so the caller fetches geometry separately for the winning stop.
    """
    if not destinations:
        return []

    # Source is index 0 (origin); destinations follow.
    coords = f"{origin_lon},{origin_lat};" + ";".join(
        f"{d['lon']},{d['lat']}" for d in destinations
    )
    url = f"{OSRM_TABLE_URL}/{coords}"
    params = {"sources": "0", "annotations": "duration,distance"}

    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("durations"):
            return [None] * len(destinations)
    except Exception:
        return [None] * len(destinations)

    durations = data["durations"][0]          # row for source 0: [self, d1, d2, ...]
    dist_rows = data.get("distances") or [[]]
    distances = dist_rows[0] if dist_rows else []

    result = []
    for i in range(len(destinations)):
        dur = durations[i + 1] if i + 1 < len(durations) else None
        if dur is None:
            result.append(None)
            continue
        dist = distances[i + 1] if i + 1 < len(distances) else None
        result.append({"duration": dur, "distance": dist})
    return result


def get_route(origin_lat, origin_lon, dest_lat, dest_lon):
    url = f"{OSRM_ROUTE_URL}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    params = {"overview": "full", "geometries": "geojson"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route = data["routes"][0]
        return {
            "duration": route["duration"],   # seconds
            "distance": route["distance"],   # metres
            "geometry": route["geometry"],   # GeoJSON LineString
        }
    except Exception:
        return None
