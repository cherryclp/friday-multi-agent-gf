# pw_schedule.py
# Physics Wallah (PW) class schedule fetcher for Jarvis
#
# WHAT THIS FILE DOES:
#   - Fetches real class schedule from PW API (api.penpencil.co)
#   - Supports: today, tomorrow, any specific date, full week (Mon-Sun)
#   - Filters out fake/filler entries (pre-recorded extras with no real duration)
#   - Converts UTC times to IST automatically
#   - Tags classes as LIVE / ended / upcoming
#
# Setup (one time only):
#   1. Open PW website, DevTools → Network → any API call → copy "Authorization" header
#   2. Tell Jarvis: "set my pw token to Bearer eyJ..."
#   3. Tell Jarvis: "set my batch id to 698ad3519549b300a5e1cc6a"

import os
import sqlite3
import requests
from datetime import datetime, timezone, timedelta

# ─── IST timezone ─────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

# ─── Config ───────────────────────────────────────────────────────────────────
JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(JARVIS_DIR, "jarvis_memory.db")

PW_BASE  = "https://api.penpencil.co"
PW_SCHED = "/v2/batches/{batch_id}/todays-schedule"

TYPE_EMOJI = {
    "LECTURE":       "🎥",
    "NOTES":         "📄",
    "BULK_SCHEDULE": "📚",
    "DPP":           "📝",
    "TEST":          "✏️",
}

TAG_EMOJI = {
    "live":     "🔴 LIVE",
    "ended":    "✅ ended",
    "upcoming": "📅 upcoming",
    "recorded": "📹 recorded",
}


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _db_get(key: str) -> str | None:
    try:
        with sqlite3.connect(DB_PATH) as con:
            row = con.execute("SELECT value FROM memory WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _db_set(key: str, value: str):
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute(
                "INSERT INTO memory(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value)
            )
    except Exception as e:
        print(f"[PW] db_set error: {e}")


def get_pw_token() -> str | None:
    return os.environ.get("PW_AUTH_TOKEN") or _db_get("_pw_auth_token")


def get_pw_batch_id() -> str | None:
    return os.environ.get("PW_BATCH_ID") or _db_get("_pw_batch_id")


def set_pw_token(token: str) -> str:
    token = token.strip()
    if not token.startswith("Bearer "):
        token = "Bearer " + token
    _db_set("_pw_auth_token", token)
    return "PW auth token saved!!"


def set_pw_batch_id(batch_id: str) -> str:
    _db_set("_pw_batch_id", batch_id.strip())
    return f"PW batch ID saved: {batch_id.strip()}"


# ─── Time helpers ─────────────────────────────────────────────────────────────

def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST)
    except Exception:
        return None


def _fmt_time(ts: str | None) -> str:
    dt = _parse_iso(ts)
    return dt.strftime("%I:%M %p") if dt else "?"


def _fmt_duration(start: str | None, end: str | None) -> str:
    s = _parse_iso(start)
    e = _parse_iso(end)
    if not s or not e or e <= s:
        return ""
    mins = int((e - s).total_seconds() // 60)
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if m else f"{h}h"


# ─── Core fetch (single day) ──────────────────────────────────────────────────

def fetch_schedule(date_str: str | None = None) -> dict:
    """
    Fetch PW schedule for a single date (YYYY-MM-DD, default = today IST).
    Returns {"ok": bool, "date": str, "items": [...], "error": str|None}
    """
    token    = get_pw_token()
    batch_id = get_pw_batch_id()

    if not token:
        return {"ok": False, "error": "no PW auth token — tell Jarvis: set my pw token to Bearer eyJ...", "date": date_str or "", "items": []}
    if not batch_id:
        return {"ok": False, "error": "no PW batch ID — tell Jarvis: set my batch id to 698...", "date": date_str or "", "items": []}

    if not date_str:
        date_str = datetime.now(IST).strftime("%Y-%m-%d")

    url = PW_BASE + PW_SCHED.format(batch_id=batch_id)
    headers = {
        "Authorization":  token,
        "Content-Type":   "application/json",
        "Client-Id":      "5eb393ee95fab7468a79d189",
        "Client-Type":    "WEB",
        "Client-Version": "4.6.5",
        "randomId":       "81ac4abf-2ead-4b88-b57a-4a4ff7c3f1ab",
        "Origin":         "https://www.pw.live",
        "Referer":        "https://www.pw.live/",
        "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    params = {"batchId": batch_id, "date": date_str}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=12)
        if resp.status_code == 401:
            return {"ok": False, "error": "PW token expired — get a fresh one from DevTools and say 'set my pw token to Bearer ...'", "date": date_str, "items": []}
        if resp.status_code == 404:
            return {"ok": False, "error": "batch not found — check your batch ID", "date": date_str, "items": []}
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return {"ok": False, "error": f"network error: {str(e)[:120]}", "date": date_str, "items": []}

    raw_items = data.get("data", [])
    items = []
    for entry in raw_items:
        etype = entry.get("type", "LECTURE")
        det   = entry.get("data") or entry.get("videoDetails") or entry.get("notesDetails") or entry.get("bulkScheduleDetails") or {}

        subject  = det.get("subjectId", {})
        sub_name = subject.get("name", "") if isinstance(subject, dict) else str(subject)
        topic    = det.get("topic", "")
        start    = det.get("startTime") or det.get("date")
        end      = det.get("endTime") or None
        raw_tag  = det.get("tag") or entry.get("tag") or ""
        tag      = raw_tag.strip().lower()
        dpp_cnt  = det.get("dppCount", 0)
        has_pdf  = det.get("hasAttachment", False) or bool(det.get("homeworkIds"))
        ex_ids   = det.get("exerciseIds") or []

        pdfs = []
        for hw in (det.get("homeworkIds") or []):
            if isinstance(hw, dict):
                for att in (hw.get("attachmentIds") or []):
                    if isinstance(att, dict) and att.get("name"):
                        pdfs.append(att["name"])

        items.append({
            "type":      etype,
            "subject":   sub_name,
            "topic":     topic,
            "start":     start,
            "end":       end,
            "tag":       tag,
            "dpp_count": dpp_cnt,
            "has_pdf":   has_pdf,
            "pdfs":      pdfs,
            "exercises": len(ex_ids),
        })

    # FIX: filter out filler/pre-recorded entries — real classes always have start != end
    items = [i for i in items if i["start"] != i["end"]]
    items.sort(key=lambda x: x["start"] or "")
    return {"ok": True, "date": date_str, "items": items, "error": None}


# ─── Week fetch ───────────────────────────────────────────────────────────────

def fetch_week_schedule(start_date: str | None = None) -> list[dict]:
    """
    Fetch schedule for 7 days starting from start_date (default = today IST).
    Returns list of fetch_schedule results, one per day.
    """
    if not start_date:
        base = datetime.now(IST)
    else:
        try:
            base = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=IST)
        except Exception:
            base = datetime.now(IST)

    results = []
    for i in range(7):
        day = base + timedelta(days=i)
        date_str = day.strftime("%Y-%m-%d")
        results.append(fetch_schedule(date_str))
    return results


# ─── Formatter (single day) ───────────────────────────────────────────────────

def format_schedule_for_jarvis(result: dict) -> str:
    """Convert a single fetch_schedule result into a chat-friendly string."""
    if not result["ok"]:
        return f"couldn't fetch ur schedule babe 😭 — {result['error']}"

    items = result["items"]
    date  = result["date"]

    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        date_label = d.strftime("%A, %d %B %Y")
    except Exception:
        date_label = date

    if not items:
        return f"no classes on {date_label} 🎉 free day!!"

    lectures = [i for i in items if i["type"] in ("LECTURE", "BULK_SCHEDULE")]
    notes    = [i for i in items if i["type"] == "NOTES"]
    others   = [i for i in items if i["type"] not in ("LECTURE", "BULK_SCHEDULE", "NOTES")]

    lines = [f"📅 schedule for {date_label}:", ""]

    if lectures:
        lines.append("── CLASSES ──────────────────────────────")
        for ix, item in enumerate(lectures, 1):
            emoji   = TYPE_EMOJI.get(item["type"], "📌")
            tag_lbl = TAG_EMOJI.get(item["tag"], item["tag"])
            dur     = _fmt_duration(item["start"], item["end"])
            time_s  = _fmt_time(item["start"])

            line = f"{ix}. {emoji} {item['subject']}"
            if item["topic"]:
                line += f"\n   📖 {item['topic']}"
            line += f"\n   🕐 {time_s}"
            if dur:
                line += f" ({dur})"
            if tag_lbl:
                line += f"  {tag_lbl}"
            extras = []
            if item["dpp_count"]:
                extras.append(f"📝 {item['dpp_count']} DPP")
            if item["exercises"]:
                extras.append(f"📋 {item['exercises']} quiz")
            if item["has_pdf"]:
                extras.append("📄 notes available")
            if extras:
                line += f"\n   {' · '.join(extras)}"
            lines.append(line)

    if notes:
        lines.append("")
        lines.append("── NOTES / PDFs ──────────────────────────")
        for item in notes:
            sub  = item["subject"] or "notes"
            pdfs = item["pdfs"]
            if pdfs:
                for pdf in pdfs:
                    lines.append(f"  📄 {pdf}")
            else:
                lines.append(f"  📄 {sub}")

    if others:
        lines.append("")
        lines.append("── OTHER ──────────────────────────────────")
        for item in others:
            lines.append(f"  {TYPE_EMOJI.get(item['type'], '📌')} {item['subject']} — {item['topic']}")

    live_now = [i for i in lectures if i["tag"] == "live"]
    upcoming = [i for i in lectures if i["tag"] == "upcoming"]
    ended    = [i for i in lectures if i["tag"] == "ended"]
    lines.append("")
    summary_bits = [f"{len(lectures)} class(es) total"]
    if live_now:
        summary_bits.append(f"🔴 {len(live_now)} LIVE right now!!")
    if upcoming:
        summary_bits.append(f"📅 {len(upcoming)} upcoming")
    if ended:
        summary_bits.append(f"✅ {len(ended)} ended")
    lines.append("  ".join(summary_bits))

    return "\n".join(lines)


# ─── Formatter (week) ─────────────────────────────────────────────────────────

def format_week_for_jarvis(week_results: list[dict]) -> str:
    """Convert a full week of results into a compact chat-friendly string."""
    lines = ["📆 your week schedule:\n"]
    has_any = False

    for result in week_results:
        date = result.get("date", "")
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
            day_label = d.strftime("%A %d %b")
        except Exception:
            day_label = date

        if not result["ok"]:
            lines.append(f"── {day_label}: couldn't fetch 😭")
            continue

        items = [i for i in result["items"] if i["type"] in ("LECTURE", "BULK_SCHEDULE")]
        if not items:
            lines.append(f"── {day_label}: free day 🎉")
            continue

        has_any = True
        lines.append(f"── {day_label} ({len(items)} class{'es' if len(items) != 1 else ''}) ──")
        for item in items:
            time_s = _fmt_time(item["start"])
            dur    = _fmt_duration(item["start"], item["end"])
            subj   = item["subject"]
            topic  = item["topic"].split("||")[0].strip() if item["topic"] else ""
            entry  = f"  🕐 {time_s}"
            if dur:
                entry += f" ({dur})"
            entry += f"  {subj}"
            if topic:
                entry += f"\n     📖 {topic}"
            lines.append(entry)
        lines.append("")

    if not has_any:
        lines.append("no classes found for this week!!")

    return "\n".join(lines)


# ─── Public API for jarvis_tools.py ──────────────────────────────────────────

def get_todays_schedule_text() -> str:
    """Called when user says 'my classes today'."""
    return format_schedule_for_jarvis(fetch_schedule())


def get_schedule_text_for_date(date_str: str) -> str:
    """Called when user says 'my classes on Monday' or provides a date."""
    return format_schedule_for_jarvis(fetch_schedule(date_str))


def get_tomorrows_schedule_text() -> str:
    """Called when user says 'my classes tomorrow'."""
    tomorrow = (datetime.now(IST) + timedelta(days=1)).strftime("%Y-%m-%d")
    return format_schedule_for_jarvis(fetch_schedule(tomorrow))


def get_weekly_schedule_text() -> str:
    """Called when user says 'my classes this week' or 'weekly schedule'."""
    return format_week_for_jarvis(fetch_week_schedule())