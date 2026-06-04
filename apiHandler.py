import requests
import time

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSRM_URL = "http://router.project-osrm.org/route/v1/foot"
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
    params = {"q": address, "format": "json", "limit": 1, "addressdetails": 1}
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


def find_nearby(lat, lon, categories, radius=1500):
    conditions = []
    for cat in categories:
        for key, val in CATEGORY_TAGS.get(cat, []):
            conditions.append(f'node["{key}"="{val}"](around:{radius},{lat},{lon});')
            conditions.append(f'way["{key}"="{val}"](around:{radius},{lat},{lon});')

    if not conditions:
        return []

    query = f"[out:json][timeout:30];({''.join(conditions)});out center 60;"

    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=35)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

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


def get_route(origin_lat, origin_lon, dest_lat, dest_lon):
    url = f"{OSRM_URL}/{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
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
