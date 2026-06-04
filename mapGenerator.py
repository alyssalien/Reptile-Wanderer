import folium
import json
import os

MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "map.html")
HOT_ROADS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hotRoads.geojson")

SPECIES_EMOJI = {"gecko": "🦎", "tortoise": "🐢", "beardie": "🦕"}
CATEGORY_LABEL = {
    "park": "公園", "grass": "草地", "forest": "森林",
    "convenience": "便利商店", "campus": "校園",
}
VOTE_JS = """
<script>
function voteReport(reportId, voteType) {
  fetch('/vote', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({report_id: reportId, vote_type: voteType})
  }).then(r => r.json()).then(d => {
    if (d.success) { alert('投票成功！感謝您的回饋。'); }
    else { alert('投票失敗，請稍後再試。'); }
  }).catch(() => alert('網路錯誤'));
}
</script>
"""


def _div_icon(emoji, size=30):
    return folium.DivIcon(
        html=f'<div style="font-size:{size}px;line-height:1;filter:drop-shadow(1px 1px 2px rgba(0,0,0,.5));">{emoji}</div>',
        icon_size=(size, size),
        icon_anchor=(size // 2, size // 2),
    )


def _numbered_icon(number, color="#2d6a4f"):
    return folium.DivIcon(
        html=(
            f'<div style="background:{color};color:white;border-radius:50%;'
            f'width:28px;height:28px;display:flex;align-items:center;'
            f'justify-content:center;font-weight:bold;font-size:14px;'
            f'box-shadow:1px 1px 3px rgba(0,0,0,.4);">{number}</div>'
        ),
        icon_size=(28, 28),
        icon_anchor=(14, 14),
    )


def _build_banners(m, weather, species):
    banners = []
    top_px = 10

    if weather.get("is_hot") and weather.get("temperature") is not None:
        banners.append(
            f'<div style="position:fixed;top:{top_px}px;left:50%;transform:translateX(-50%);'
            f'background:#e85d04;color:#fff;padding:7px 18px;border-radius:20px;'
            f'font-weight:bold;z-index:9999;font-size:13px;'
            f'box-shadow:2px 2px 8px rgba(0,0,0,.35);">'
            f'⚠️ 警告：目前氣溫 {weather["temperature"]}°C — 無遮蔭路段已以紅色標示</div>'
        )
        top_px += 40

    uv = weather.get("uv_index", 0) or 0
    if species == "gecko" and uv >= 6:
        banners.append(
            f'<div style="position:fixed;top:{top_px}px;left:50%;transform:translateX(-50%);'
            f'background:#9b59b6;color:#fff;padding:7px 18px;border-radius:20px;'
            f'font-weight:bold;z-index:9999;font-size:13px;'
            f'box-shadow:2px 2px 8px rgba(0,0,0,.35);">'
            f'☀️ 紫外線指數 {uv:.1f} — 夜行性守宮請避免長時間日曬！</div>'
        )
        top_px += 40

    humidity = weather.get("humidity")
    if species == "gecko" and humidity is not None and humidity < 40:
        banners.append(
            f'<div style="position:fixed;top:{top_px}px;left:50%;transform:translateX(-50%);'
            f'background:#3a86ff;color:#fff;padding:7px 18px;border-radius:20px;'
            f'font-weight:bold;z-index:9999;font-size:13px;'
            f'box-shadow:2px 2px 8px rgba(0,0,0,.35);">'
            f'💧 濕度偏低 ({humidity}%) — 建議攜帶噴水瓶為守宮保濕</div>'
        )

    for banner in banners:
        m.get_root().html.add_child(folium.Element(banner))


def build_map(origin, ranked_candidates, species, weather,
              danger_reports, recommend_reports,
              top_geometry=None, multi_stop_routes=None, multi_stop_ordered=None):
    os.makedirs(os.path.dirname(MAP_PATH), exist_ok=True)

    m = folium.Map(
        location=[origin["lat"], origin["lon"]],
        zoom_start=15,
        tiles="OpenStreetMap",
    )

    _build_banners(m, weather, species)

    # --- Origin marker ---
    folium.Marker(
        location=[origin["lat"], origin["lon"]],
        popup=folium.Popup(
            f"<b>📍 出發點</b><br><small>{origin.get('display_name','')[:60]}</small>",
            max_width=220,
        ),
        icon=_div_icon("📍", size=34),
        tooltip="出發點",
    ).add_to(m)

    # --- Hot-road circles (when temperature >= 32°C) ---
    if weather.get("is_hot") and os.path.exists(HOT_ROADS_PATH):
        with open(HOT_ROADS_PATH, "r", encoding="utf-8") as f:
            hot_roads = json.load(f)
        for feature in hot_roads.get("features", []):
            geom = feature.get("geometry", {})
            props = feature.get("properties", {})
            if geom.get("type") == "LineString":
                coords = geom["coordinates"]
                if not coords:
                    continue
                center_lat = sum(c[1] for c in coords) / len(coords)
                center_lon = sum(c[0] for c in coords) / len(coords)
                folium.Circle(
                    location=[center_lat, center_lon],
                    radius=60,
                    color="#d62828",
                    fill=True,
                    fill_color="#d62828",
                    fill_opacity=0.30,
                    popup=folium.Popup(
                        f"🔥 高溫路段<br><small>{props.get('name','')}</small>", max_width=160
                    ),
                    tooltip="⚠️ 高溫無遮蔭路段",
                ).add_to(m)

    # --- Single top-route polyline ---
    if (top_geometry and top_geometry.get("coordinates")
            and multi_stop_routes is None):
        coords = [[c[1], c[0]] for c in top_geometry["coordinates"]]
        folium.PolyLine(
            locations=coords,
            color="#2ecc71",
            weight=5,
            opacity=0.85,
            tooltip="推薦步行路線（第 1 名目的地）",
        ).add_to(m)

    # --- Multi-stop route polylines ---
    if multi_stop_routes:
        for i, geom in enumerate(multi_stop_routes):
            if geom and geom.get("coordinates"):
                coords = [[c[1], c[0]] for c in geom["coordinates"]]
                folium.PolyLine(
                    locations=coords,
                    color="#27ae60",
                    weight=4,
                    opacity=0.80,
                    tooltip=f"段落 {i + 1}",
                ).add_to(m)

    # --- Destination markers (top 10) ---
    species_emoji = SPECIES_EMOJI.get(species, "🐾")
    display_candidates = ranked_candidates[:10]

    for i, c in enumerate(display_candidates):
        rank = f"#{i + 1} "
        shaded_txt = "✅ 有遮蔭" if c.get("is_shaded") else "☀️ 無遮蔭"
        popup_html = (
            f"<div style='min-width:160px;'>"
            f"<b>{rank}{c['name']}</b><br>"
            f"🏷️ {CATEGORY_LABEL.get(c['category'], c['category'])}&nbsp;&nbsp;{shaded_txt}<br>"
            f"🚶 步行時間: <b>{c['walk_min']:.1f} 分鐘</b><br>"
            f"📏 距離: {c.get('distance_m', 0):.0f} 公尺<br>"
            f"⭐ 評分: {c.get('rating') or '（無資料）'}"
            f"</div>"
        )
        folium.Marker(
            location=[c["lat"], c["lon"]],
            popup=folium.Popup(popup_html, max_width=210),
            icon=_div_icon(species_emoji, size=28),
            tooltip=f"{rank}{c['name']} ({c['walk_min']:.1f} 分)",
        ).add_to(m)

    # --- Multi-stop numbered markers ---
    if multi_stop_ordered:
        for i, stop in enumerate(multi_stop_ordered):
            folium.Marker(
                location=[stop["lat"], stop["lon"]],
                popup=folium.Popup(
                    f"<b>站點 {i + 1}：{stop['name']}</b>", max_width=180
                ),
                icon=_numbered_icon(i + 1),
                tooltip=f"站點 {i + 1}: {stop['name']}",
            ).add_to(m)

    # --- Community danger markers ---
    for dr in danger_reports:
        popup_html = (
            f"<div style='min-width:180px;'>"
            f"<b>⚠️ 危險回報</b><br>"
            f"📍 {dr.get('location_name','')}<br>"
            f"📝 {dr.get('description','')}<br>"
            f"👍 {dr.get('upvotes',0)}&nbsp;&nbsp;👎 {dr.get('downvotes',0)}<br>"
            f"<button onclick=\"voteReport('{dr['id']}','up')\" "
            f"style='margin:3px 2px;padding:3px 10px;cursor:pointer;border-radius:4px;'>👍 讚</button>"
            f"<button onclick=\"voteReport('{dr['id']}','down')\" "
            f"style='margin:3px 2px;padding:3px 10px;cursor:pointer;border-radius:4px;'>👎 倒讚</button>"
            f"</div>"
        )
        folium.Marker(
            location=[dr["lat"], dr["lon"]],
            popup=folium.Popup(popup_html, max_width=230),
            icon=_div_icon("🐕", size=28),
            tooltip=f"⚠️ {dr.get('location_name','危險')}",
        ).add_to(m)

    # --- Community recommend markers ---
    for rr in recommend_reports:
        popup_html = (
            f"<div style='min-width:180px;'>"
            f"<b>✅ 推薦回報</b><br>"
            f"📍 {rr.get('location_name','')}<br>"
            f"📝 {rr.get('description','')}<br>"
            f"👍 {rr.get('upvotes',0)}&nbsp;&nbsp;👎 {rr.get('downvotes',0)}<br>"
            f"<button onclick=\"voteReport('{rr['id']}','up')\" "
            f"style='margin:3px 2px;padding:3px 10px;cursor:pointer;border-radius:4px;'>👍 讚</button>"
            f"<button onclick=\"voteReport('{rr['id']}','down')\" "
            f"style='margin:3px 2px;padding:3px 10px;cursor:pointer;border-radius:4px;'>👎 倒讚</button>"
            f"</div>"
        )
        folium.Marker(
            location=[rr["lat"], rr["lon"]],
            popup=folium.Popup(popup_html, max_width=230),
            icon=_div_icon("🌿", size=28),
            tooltip=f"✅ {rr.get('location_name','推薦')}",
        ).add_to(m)

    m.get_root().html.add_child(folium.Element(VOTE_JS))
    m.save(MAP_PATH)
    return MAP_PATH
