import json
import os
import time
import uuid

REPORTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reports.json")
REPORT_TTL = 3 * 3600       # 3 hours default lifetime
UPVOTE_THRESHOLD = 5        # upvotes needed to extend lifetime
DOWNVOTE_THRESHOLD = 3      # downvotes needed for early removal
EXTEND_DURATION = 3600      # extra 1 hour per upvote extension


def _load():
    if not os.path.exists(REPORTS_FILE):
        return []
    try:
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return []


def _save(reports):
    os.makedirs(os.path.dirname(REPORTS_FILE), exist_ok=True)
    tmp = REPORTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REPORTS_FILE)  # atomic replace avoids corruption


def _is_active(report, now):
    age = now - report.get("timestamp", 0)
    extensions = report.get("upvotes", 0) // UPVOTE_THRESHOLD
    effective_ttl = REPORT_TTL + extensions * EXTEND_DURATION
    return age <= effective_ttl and report.get("downvotes", 0) < DOWNVOTE_THRESHOLD


def add_report(data):
    for attempt in range(5):
        try:
            reports = _load()
            report = {
                "id": str(uuid.uuid4()),
                "location_name": str(data.get("location_name", "未知地點"))[:100],
                "report_type": data.get("report_type", "danger"),
                "description": str(data.get("description", ""))[:300],
                "lat": float(data.get("lat", 24.7953)),
                "lon": float(data.get("lon", 120.9962)),
                "timestamp": time.time(),
                "upvotes": 0,
                "downvotes": 0,
            }
            reports.append(report)
            _save(reports)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def get_active_reports():
    reports = _load()
    now = time.time()
    danger, recommend = [], []
    for r in reports:
        if not _is_active(r, now):
            continue
        if r.get("report_type") == "danger":
            danger.append(r)
        elif r.get("report_type") == "recommend":
            recommend.append(r)
    return danger, recommend


def vote_report(report_id, vote_type):
    for attempt in range(5):
        try:
            reports = _load()
            for r in reports:
                if r["id"] == report_id:
                    if vote_type == "up":
                        r["upvotes"] = r.get("upvotes", 0) + 1
                    elif vote_type == "down":
                        r["downvotes"] = r.get("downvotes", 0) + 1
                    break
            _save(reports)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def purge_old():
    try:
        reports = _load()
        now = time.time()
        active = [r for r in reports if _is_active(r, now)]
        if len(active) < len(reports):
            _save(active)
    except Exception:
        pass
