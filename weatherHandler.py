import requests
import time

HSINCHU_LAT = 24.80
HSINCHU_LON = 120.97
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_TTL = 600  # 10 minutes

_cache = {"data": None, "timestamp": 0}


def get_weather():
    now = time.time()
    if _cache["data"] and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["data"]

    params = {
        "latitude": HSINCHU_LAT,
        "longitude": HSINCHU_LON,
        "current": "temperature_2m,relative_humidity_2m,uv_index",
        "timezone": "Asia/Taipei",
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=8)
        resp.raise_for_status()
        j = resp.json()
        current = j.get("current", {})

        temp = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")
        uv = current.get("uv_index", 0)

        data = {
            "temperature": temp,
            "humidity": humidity,
            "uv_index": uv if uv is not None else 0,
            "is_hot": (temp is not None and temp >= 32),
        }
        _cache["data"] = data
        _cache["timestamp"] = now
        return data

    except Exception:
        # Safe defaults when API is unreachable
        fallback = {"temperature": None, "humidity": None, "uv_index": 0, "is_hot": False}
        _cache["data"] = fallback
        _cache["timestamp"] = now
        return fallback


def invalidate_cache():
    _cache["data"] = None
    _cache["timestamp"] = 0
