from math import radians, cos, sin, asin, sqrt

# Per-species bonus scores for each OSM category
SPECIES_CATEGORY_BONUS = {
    "gecko": {
        "park":        3.0,
        "forest":      4.5,
        "grass":       1.0,
        "convenience": 0.3,
        "campus":      1.5,
    },
    "tortoise": {
        "park":        2.5,
        "forest":      0.5,
        "grass":       4.5,
        "convenience": 0.5,
        "campus":      1.0,
    },
    "beardie": {
        "park":        2.0,
        "forest":      1.5,
        "grass":       3.5,
        "convenience": 0.5,
        "campus":      2.0,
    },
}

HOT_UNSHADED_PENALTY_GECKO = 3.5   # gecko in hot weather avoids unshaded spots
DOG_DANGER_RADIUS_M = 200          # beardie avoids dog reports within this radius
DOG_PENALTY_BEARDIE = 5.0
GRASS_BONUS_TORTOISE_RADIUS_M = 100
GRASS_BONUS_TORTOISE = 2.5
GENERAL_DANGER_RADIUS_M = 150
GENERAL_DANGER_PENALTY = 2.0
GENERAL_RECOMMEND_RADIUS_M = 100
GENERAL_RECOMMEND_BONUS = 1.5

# UV thresholds
UV_HIGH_GECKO = 6    # warn / penalise unshaded spots for nocturnal gecko
UV_GOOD_BEARDIE = 3  # beardie and tortoise benefit from moderate UV exposure

# OSM rating contributes to the score instead of overriding it. Most OSM places
# have no rating (0); a 5-star place gets +2.0 — comparable to a category bonus,
# so it nudges ranking without burying a better species-fit destination.
RATING_WEIGHT = 0.4


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(max(0, a)))


def _species_score(candidate, species, is_hot, uv_index, danger_reports, recommend_reports):
    score = SPECIES_CATEGORY_BONUS.get(species, {}).get(candidate.get("category", "park"), 0.5)
    is_shaded = candidate.get("is_shaded", False)
    c_lat, c_lon = candidate["lat"], candidate["lon"]

    # Gecko: penalise unshaded spots when hot or UV is high
    if species == "gecko":
        if is_hot and not is_shaded:
            score -= HOT_UNSHADED_PENALTY_GECKO
        if uv_index >= UV_HIGH_GECKO and not is_shaded:
            score -= 2.0

    # Beardie: strongly avoids dog danger reports within 200 m
    if species == "beardie":
        for dr in danger_reports:
            if "dog" in dr.get("description", "").lower() or "狗" in dr.get("description", ""):
                dist = _haversine_m(c_lat, c_lon, dr["lat"], dr["lon"])
                if dist <= DOG_DANGER_RADIUS_M:
                    score -= DOG_PENALTY_BEARDIE

    # Tortoise: bonus near grass recommend reports
    if species == "tortoise":
        for rr in recommend_reports:
            dist = _haversine_m(c_lat, c_lon, rr["lat"], rr["lon"])
            if dist <= GRASS_BONUS_TORTOISE_RADIUS_M:
                score += GRASS_BONUS_TORTOISE

    # All species: generic danger / recommend adjustments
    for dr in danger_reports:
        if _haversine_m(c_lat, c_lon, dr["lat"], dr["lon"]) <= GENERAL_DANGER_RADIUS_M:
            score -= GENERAL_DANGER_PENALTY

    for rr in recommend_reports:
        if _haversine_m(c_lat, c_lon, rr["lat"], rr["lon"]) <= GENERAL_RECOMMEND_RADIUS_M:
            score += GENERAL_RECOMMEND_BONUS

    return score


def filter_and_rank(candidates, species, max_walk_min, is_hot, uv_index, humidity,
                    danger_reports, recommend_reports):
    # Filter: only keep destinations with valid OSRM walk times within threshold
    valid = [c for c in candidates if c.get("walk_min") is not None and c["walk_min"] <= max_walk_min]

    for c in valid:
        base = _species_score(c, species, is_hot, uv_index, danger_reports, recommend_reports)
        # Fold the (usually sparse) OSM rating in as a bonus rather than the top key
        c["species_score"] = base + c.get("rating", 0) * RATING_WEIGHT

    # Sort by: 1) species_score desc (rating already baked in)  2) walk_min asc
    valid.sort(key=lambda c: (-c["species_score"], c["walk_min"]))
    return valid


def optimize_multi_stop(origin_lat, origin_lon, stops):
    """Nearest-Neighbour Heuristic: order stops to minimise total walk time."""
    if not stops:
        return []

    unvisited = list(stops)
    ordered = []
    cur_lat, cur_lon = origin_lat, origin_lon

    while unvisited:
        # Use precomputed walk_min if available; fall back to straight-line proxy
        def cost(s):
            if s.get("walk_min") is not None:
                return s["walk_min"]
            return _haversine_m(cur_lat, cur_lon, s["lat"], s["lon"])

        nearest = min(unvisited, key=cost)
        ordered.append(nearest)
        cur_lat, cur_lon = nearest["lat"], nearest["lon"]
        unvisited.remove(nearest)

    return ordered
