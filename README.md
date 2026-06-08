# 🦎 Reptile Wanderer — 爬蟲類散步友善地圖

> 專為爬蟲類寵物主人打造的智慧散步路線規劃工具。輸入起點地址，依「物種需求 + 即時天氣 + 社群回報」推薦最適合帶守宮、陸龜、鬃獅蜥散步的鄰近地點，並用真實步行路徑（非直線距離）排序與導航。

---

## 目錄
- [專案特色](#專案特色)
- [系統架構](#系統架構)
- [快速開始](#快速開始)
- [使用方式](#使用方式)
- [模組說明](#模組說明)
- [API 路由](#api-路由)
- [評分演算法](#評分演算法)
- [外部服務](#外部服務)
- [資料檔案](#資料檔案)
- [已知限制與改進建議](#已知限制與改進建議)

---

## 專案特色

| 功能 | 說明 |
|------|------|
| 🦎 **物種感知排序** | 守宮 / 陸龜 / 鬃獅蜥各有不同的地點偏好與天氣敏感度 |
| 🌳 **多類別地點搜尋** | 公園、草地、森林、校園、便利商店（資料來自 OpenStreetMap） |
| 🚶 **真實步行時間** | 透過 OSRM 計算實際步行路徑與時間，而非直線距離 |
| 🌡️ **即時天氣連動** | 串接 Open-Meteo，高溫 / 高 UV / 低濕度時給出物種化警示 |
| 🔥 **高溫路段警示** | 氣溫 ≥ 32°C 時於地圖標示無遮蔭的高溫路段 |
| 📢 **社群即時回報** | 使用者可回報「危險」（如未牽繩犬隻）或「推薦」地點，帶投票與自動過期機制 |
| 🗺️ **多站點路線規劃** | 勾選多個目的地，以最近鄰演算法排出最佳散步順序 |

---

## 系統架構

```
瀏覽器 (templates/index.html + static/style.css)
        │  fetch JSON
        ▼
   Flask (app.py)  ── 路由協調層
        │
        ├── apiHandler.py        Nominatim 地理編碼 / Overpass 找地點 / OSRM 路徑
        ├── weatherHandler.py    Open-Meteo 即時天氣（含 10 分鐘快取）
        ├── communityHandler.py  社群回報 CRUD + 投票 + TTL 過期（JSON 檔儲存）
        ├── routeFilter.py       物種化評分排序 + 多站點最佳化
        └── mapGenerator.py      以 Folium 產生互動地圖 (static/map.html)
                                     │
                                     ▼
                          iframe 載入 /map 顯示地圖
```

**資料流（一次搜尋）：** 地址 → 地理編碼 → Overpass 找鄰近地點 → 一次 OSRM `table` 取得所有地點步行時間 → 抓天氣 → 載入社群回報 → 物種化評分排序 → 為第 1 名抓取路徑線形 → 產生 Folium 地圖 → 回傳結果 JSON。

---

## 快速開始

### 環境需求
- Python 3.9+
- 可連外網路（需呼叫 Nominatim / Overpass / OSRM / Open-Meteo 公開 API）

### 安裝與執行

```bash
# 1. 安裝相依套件
pip install -r requirements.txt

# 2. 啟動服務
python app.py

# 3. 開啟瀏覽器
#    http://127.0.0.1:5001
```

服務預設跑在 **port 5001**。`static/` 與 `data/` 目錄會在啟動時自動建立。

可用環境變數調整：`HOST`（預設 `127.0.0.1`）、`PORT`（預設 `5001`）、`FLASK_DEBUG`（`1` 開啟除錯，預設關閉）。

### 使用 Docker（建議部署方式）

```bash
# 方式 A：docker compose（一鍵啟動，含 data 持久化）
docker compose up --build
#   → http://localhost:5001

# 方式 B：純 docker
docker build -t reptile-wanderer .
docker run -p 5001:5001 -v "$(pwd)/data:/app/data" reptile-wanderer
```

容器以 **gunicorn** 啟動。⚠️ 刻意只開 **1 個 worker**：`app._last_ctx` 是記憶體中的全域單例(多站點規劃與回報後重繪都依賴它),多 worker 會看不到彼此的搜尋狀態。社群回報資料透過 volume 掛載 `data/` 持久化。

---

## 使用方式

1. **輸入起點**：在搜尋框輸入地址或地標（例如「清華大學台達館」）。
2. **選擇物種**：守宮 🦎 / 陸龜 🐢 / 鬃獅蜥 🦕，影響排序權重與天氣警示。
3. **設定條件**：最長步行時間（5/10/15/20 分）與地點類別（可複選）。
4. **按下搜尋**：地圖標出推薦地點與第一名的步行路線，下方列出排序結果。
5. **多站點規劃**（選用）：勾選 2 個以上地點 → 「規劃多站點最佳路線」，取得建議散步順序與總時間。
6. **社群回報**（選用）：展開「📢 社群回報」面板，回報危險或推薦地點；其他使用者可對回報投票。

---

## 模組說明

| 檔案 | 職責 |
|------|------|
| `app.py` | Flask 進入點，定義所有路由，協調各模組；以 **per-session** 快取(cookie `rw_sid` + 記憶體 context)支援多人各自的搜尋狀態與地圖，閒置 1 小時自動清除 |
| `apiHandler.py` | 外部地理服務封裝：`geocode`（地址→座標）、`geocode_near`（帶 viewbox 偏好的回報定位）、`find_nearby`（Overpass 找地點）、`get_route_table`（一次 OSRM 取多點步行時間）、`get_route`（OSRM 單條路徑線形） |
| `weatherHandler.py` | `get_weather()` 取得新竹即時天氣，含 10 分鐘記憶體快取與斷線安全預設值 |
| `communityHandler.py` | 社群回報儲存於 **SQLite**（`data/reports.db`，WAL 模式）、投票、TTL 過期與清理；首次啟動會自動把舊的 `reports.json` 遷移進資料庫 |
| `routeFilter.py` | `filter_and_rank()` 物種化評分排序、`optimize_multi_stop()` 最近鄰多站點排序、Haversine 距離工具 |
| `mapGenerator.py` | 以 Folium 組裝互動地圖：起點、推薦地點、路線、高溫路段、社群標記與天氣橫幅 |
| `templates/index.html` | 單頁前端 UI 與所有互動 JS（搜尋 / 排序卡片 / 多站點 / 回報 / 地圖刷新） |
| `static/style.css` | 綠色系視覺主題與 RWD 樣式 |

---

## API 路由

| 方法 | 路徑 | 說明 | 主要回傳 |
|------|------|------|----------|
| `GET`  | `/` | 首頁 | HTML |
| `POST` | `/search` | 主搜尋：地理編碼 + 找地點 + 路徑 + 排序 | `results[]`, `weather`, `origin`, `total_found` |
| `GET`  | `/map` | 回傳最新產生的 Folium 地圖 | HTML |
| `POST` | `/optimize` | 多站點最佳路線（需先搜尋過） | `ordered_stops[]`, `total_walk_min` |
| `POST` | `/report` | 新增社群回報（自動地理編碼地點名稱） | `success`, `message` |
| `POST` | `/vote` | 對回報投票（`up` / `down`） | `success` |

**請求範例（/search）：**
```json
{
  "address": "清華大學台達館",
  "species": "gecko",
  "max_walk_min": 15,
  "categories": ["park", "grass"]
}
```

---

## 評分演算法

排序鍵（`routeFilter.filter_and_rank`）依序為：
1. **物種分數** `species_score` 高者優先（已將 OSM 評分以 `RATING_WEIGHT=0.4` 加權併入）
2. **步行時間** `walk_min` 短者優先

`species_score` 由「物種 × 類別基礎分」起算，再依情境加減，最後加上 `rating × 0.4`（5 星約 +2.0，與一個類別加分相當，能微調排序但不會壓過更適合該物種的地點）：

| 規則 | 影響物種 | 調整 |
|------|----------|------|
| 高溫且無遮蔭 | 守宮 | −3.5 |
| UV ≥ 6 且無遮蔭 | 守宮 | −2.0 |
| 200m 內有犬隻危險回報 | 鬃獅蜥 | −5.0 |
| 100m 內有推薦回報（草地） | 陸龜 | +2.5 |
| 150m 內有任意危險回報 | 全部 | −2.0 |
| 100m 內有任意推薦回報 | 全部 | +1.5 |

> 設計理念：守宮夜行性、怕高溫高 UV、需保濕；陸龜偏好乾淨草地；鬃獅蜥需曬背但怕犬隻。

---

## 外部服務

| 服務 | 用途 | 備註 |
|------|------|------|
| [Nominatim](https://nominatim.openstreetmap.org) | 地址 → 座標 | 公開 API 限 1 req/sec，程式內已 `sleep(1)` |
| [Overpass API](https://overpass-api.de) | 依 OSM 標籤找鄰近地點 | 預設搜尋半徑 1500m |
| [OSRM](http://router.project-osrm.org) | 步行路徑與時間 | demo server，僅供測試用途 |
| [Open-Meteo](https://open-meteo.com) | 即時天氣 | 免費、免金鑰 |

> ⚠️ 以上皆為公開 demo / 免費服務，有流量限制且不保證可用性，不建議用於正式生產環境。

---

## 資料檔案

| 檔案 | 內容 |
|------|------|
| `data/reports.db` | 社群回報 SQLite 資料庫（執行時自動建立，預設 3 小時 TTL，可由投票延長 / 提前移除；已列入 `.gitignore`） |
| `data/reports.json` | 舊版回報資料，首次啟動會自動遷移進 `reports.db`，之後不再使用 |
| `data/hotRoads.geojson` | 預先定義的高溫無遮蔭路段（新竹清大周邊），高溫時於地圖標示 |
| `static/map.html` | 預設歡迎地圖；`static/maps/<sid>.html` 為各 session 的地圖（皆列入 `.gitignore`） |

---

## 測試

```bash
pip install -r requirements-dev.txt
pytest
```

涵蓋社群回報 SQLite 邏輯(CRUD / 投票 / TTL 過期 / 舊資料遷移)、物種評分與多站點排序、OSRM `table` 解析、天氣快取與斷線 fallback,以及 Flask 路由(全程 mock 外部 API,不需連網)。

---

## 已知限制與改進建議

- **Session context 存於記憶體**：已支援多人各自的搜尋狀態(per-session),但 context 存在 worker 記憶體中,所以 Docker 仍只開 **1 個 gunicorn worker**;要水平擴展需改用 Redis 等共享儲存。
- **公開 demo API**：Nominatim / Overpass / OSRM 皆為公開服務，有流量限制、無 SLA，不適合正式上線。
- **OSM 評分欄位稀疏**：多數地點無 `stars`/`rating`，排序主要由物種分數決定（評分僅作加權微調）。
- **高溫路段為靜態資料**：`hotRoads.geojson` 僅涵蓋新竹清大周邊，換城市時不會有對應的高溫路段標示。

詳細的功能擴充與工程改善規劃，請見隨附的 proposal 說明。

---

## 開發者

113590030 鄧伊翎 &nbsp;&amp;&nbsp; 113590037 張婕茵

資料來源：© OpenStreetMap contributors｜路徑：OSRM｜天氣：Open-Meteo
