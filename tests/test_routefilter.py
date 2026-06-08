"""Species scoring, rating folding, walk filter, and multi-stop ordering."""
import routeFilter as rf


def _c(**over):
    base = {"name": "x", "lat": 24.80, "lon": 121.00, "category": "park",
            "is_shaded": True, "rating": 0, "walk_min": 5}
    base.update(over)
    return base


def test_gecko_hot_unshaded_penalised():
    spot = _c(is_shaded=False)
    hot = rf._species_score(dict(spot), "gecko", True, 0, [], [])
    cool = rf._species_score(dict(spot), "gecko", False, 0, [], [])
    assert hot < cool


def test_gecko_high_uv_unshaded_penalised():
    spot = _c(is_shaded=False)
    high = rf._species_score(dict(spot), "gecko", False, rf.UV_HIGH_GECKO, [], [])
    low = rf._species_score(dict(spot), "gecko", False, 0, [], [])
    assert high < low


def test_rating_breaks_tie():
    cands = [_c(name="A", rating=5), _c(name="B", rating=0)]
    ranked = rf.filter_and_rank(cands, "gecko", 15, False, 0, 60, [], [])
    assert ranked[0]["name"] == "A"   # rating bonus wins an otherwise-equal pair


def test_rating_does_not_override_species_fit():
    # forest base 4.5 should still beat park base 3.0 + (1*0.4) = 3.4
    cands = [_c(name="forest", category="forest", rating=0),
             _c(name="park", category="park", rating=1)]
    ranked = rf.filter_and_rank(cands, "gecko", 15, False, 0, 60, [], [])
    assert ranked[0]["name"] == "forest"


def test_walk_time_filter_excludes_far():
    cands = [_c(walk_min=30)]
    assert rf.filter_and_rank(cands, "gecko", 15, False, 0, 60, [], []) == []


def test_beardie_avoids_dog_reports():
    spot = _c(category="grass", is_shaded=False)
    dog = [{"lat": 24.80, "lon": 121.00, "description": "三隻沒牽繩的狗 dog"}]
    near = rf._species_score(dict(spot), "beardie", False, 0, dog, [])
    none = rf._species_score(dict(spot), "beardie", False, 0, [], [])
    assert near < none


def test_tortoise_grass_recommend_bonus():
    spot = _c(category="grass")
    rec = [{"lat": 24.80, "lon": 121.00, "description": "乾淨草地"}]
    with_rec = rf._species_score(dict(spot), "tortoise", False, 0, [], rec)
    without = rf._species_score(dict(spot), "tortoise", False, 0, [], [])
    assert with_rec > without


def test_optimize_nearest_neighbour_order():
    stops = [{"name": "far", "lat": 24.90, "lon": 121.0, "walk_min": 20},
             {"name": "near", "lat": 24.81, "lon": 121.0, "walk_min": 3}]
    ordered = rf.optimize_multi_stop(24.80, 121.0, stops)
    assert ordered[0]["name"] == "near"


def test_optimize_empty():
    assert rf.optimize_multi_stop(24.8, 121.0, []) == []


def test_haversine_zero_distance():
    assert rf._haversine_m(24.8, 121.0, 24.8, 121.0) == 0


# --- Suitability 1–5 rating ---------------------------------------------------
def test_suitability_within_1_to_5():
    cands = [_c(name="ideal", category="forest", is_shaded=True),
             _c(name="poor", category="convenience", is_shaded=False)]
    ranked = rf.filter_and_rank(cands, "gecko", 15, True, 8, 60, [], [])
    for c in ranked:
        assert 1.0 <= c["suitability"] <= 5.0


def test_better_fit_scores_higher():
    cands = [_c(name="shade", category="forest", is_shaded=True),
             _c(name="sun", category="grass", is_shaded=False)]
    ranked = rf.filter_and_rank(cands, "gecko", 15, False, 0, 60, [], [])
    by_name = {c["name"]: c["suitability"] for c in ranked}
    assert by_name["shade"] > by_name["sun"]   # ideal gecko habitat rated higher


# --- Per-species habitat preference (rank, don't exclude) ---------------------
def test_gecko_prefers_shade_but_keeps_unshaded():
    cands = [_c(name="sun", is_shaded=False), _c(name="shade", is_shaded=True)]
    names = [c["name"] for c in rf.filter_and_rank(cands, "gecko", 15, False, 0, 60, [], [])]
    assert names == ["shade", "sun"]   # shaded first, unshaded still listed (not empty)


def test_tortoise_prefers_flat_terrain():
    cands = [_c(name="forest", category="forest"), _c(name="grass", category="grass")]
    ranked = rf.filter_and_rank(cands, "tortoise", 15, False, 0, 60, [], [])
    assert ranked[0]["name"] == "grass"   # flat ground ranks above uneven forest
    assert len(ranked) == 2               # forest still present


def test_beardie_deprioritises_busy_convenience():
    cands = [_c(name="shop", category="convenience"), _c(name="park", category="park")]
    ranked = rf.filter_and_rank(cands, "beardie", 15, False, 0, 60, [], [])
    assert ranked[0]["name"] == "park"    # quiet park first
    assert len(ranked) == 2               # busy shop still present
