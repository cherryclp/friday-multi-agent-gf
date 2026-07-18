# scheduler.py
# Jarvis Scheduled Task System — like cron but cuter 💕
# Supports: "every 30 mins", "every day at 9am", "every monday at 10:30", "in 5 minutes"

import sqlite3
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
import calendar

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(JARVIS_DIR, "jarvis_memory.db")
_sched_lock = threading.Lock()
_sched_callback = None  # set by jarvis.py for UI push
_sched_stop = threading.Event()


def _init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                schedule_type TEXT NOT NULL,  -- interval / daily / weekly / one_shot
                schedule_value TEXT NOT NULL,  -- JSON: {minutes:30} or {time:"09:00",days:["monday"]}
                steps TEXT NOT NULL DEFAULT '[]',  -- JSON array of step objects
                enabled INTEGER DEFAULT 1,
                profile TEXT DEFAULT 'cherry',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                last_run TEXT,
                next_run TEXT,
                run_count INTEGER DEFAULT 0,
                UNIQUE(name, profile)
            );
        """)

_init_db()


# ── NATURAL LANGUAGE SCHEDULE PARSING ──────────────────────────────

_DAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def parse_schedule(text: str):
    """Parse a natural language schedule string.
    Returns (schedule_type, schedule_value_dict) or raises ValueError.
    """
    t = text.lower().strip().rstrip(".")

    # ── "in X minutes/hours" → one_shot ─────────────────────────
    m = re.match(r"in\s+(\d+)\s+(min(?:ute)?s?|hour?s?|second?s?)\s*$", t)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("second"):
            return "one_shot", {"seconds": num}
        elif unit.startswith("min"):
            return "one_shot", {"minutes": num}
        else:
            return "one_shot", {"hours": num}

    # ── "every X minutes/hours/days" → interval ─────────────────
    m = re.match(r"every\s+(\d+)\s+(min(?:ute)?s?|hour?s?|day?s?)\s*$", t)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("min"):
            return "interval", {"minutes": num}
        elif unit.startswith("hour"):
            return "interval", {"hours": num}
        else:
            return "interval", {"days": num}

    # ── "every X seconds" (special case) ─────────────────────────
    m = re.match(r"every\s+(\d+)\s+second?s?\s*$", t)
    if m:
        return "interval", {"seconds": int(m.group(1))}

    # ── "every day at HH:MM" / "daily at HH:MM" → daily ─────────
    m = re.match(r"(?:every\s+)?(?:day|daily)\s+(?:at\s+)?(\d{1,2}):?(\d{2})\s*(am|pm)?", t)
    if m:
        hour, minute, ampm = _normalize_time(m.group(1), m.group(2), m.group(3))
        return "daily", {"hour": hour, "minute": minute}

    # ── "every weekday at HH:MM" → weekdays daily ────────────────
    m = re.match(r"(?:every\s+)?(?:weekday|weekdays|week day)\s+(?:at\s+)?(\d{1,2}):?(\d{2})\s*(am|pm)?", t)
    if m:
        hour, minute, ampm = _normalize_time(m.group(1), m.group(2), m.group(3))
        return "daily", {"hour": hour, "minute": minute, "days": [0, 1, 2, 3, 4]}

    # ── "every monday at HH:MM" / "every mon at HH:MM" → weekly ─
    m = re.match(r"(?:every\s+)?(\w+)\s+(?:at\s+)?(\d{1,2}):?(\d{2})\s*(am|pm)?", t)
    if m:
        day_name = m.group(1).lower()
        if day_name in _DAYS:
            hour, minute, ampm = _normalize_time(m.group(2), m.group(3), m.group(4))
            return "weekly", {"hour": hour, "minute": minute, "days": [_DAYS[day_name]]}

    # ── "every monday and wednesday at HH:MM" → multi-day weekly
    m = re.match(r"every\s+(.+?)\s+(?:at\s+)?(\d{1,2}):?(\d{2})\s*(am|pm)?", t)
    if m:
        day_part = m.group(1).lower().strip()
        hour, minute, ampm = _normalize_time(m.group(2), m.group(3), m.group(4))
        # Check for "X and Y" pattern
        day_names = re.split(r"\s+(?:and|,|\s)\s+", day_part)
        day_nums = []
        for dn in day_names:
            dn = dn.strip()
            if dn in _DAYS:
                day_nums.append(_DAYS[dn])
        if day_nums:
            return "weekly", {"hour": hour, "minute": minute, "days": day_nums}

    raise ValueError(f"couldn't parse schedule: '{text}'")


def _normalize_time(hour_str, minute_str, ampm):
    hour = int(hour_str)
    minute = int(minute_str)
    if ampm:
        ampm = ampm.lower().strip()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
    return hour, minute, ampm


def compute_next_run(schedule_type, schedule_value, from_time=None):
    """Calculate the next datetime this schedule should fire."""
    if from_time is None:
        from_time = datetime.now()

    if schedule_type == "one_shot":
        seconds = schedule_value.get("seconds", 0)
        minutes = schedule_value.get("minutes", 0)
        hours = schedule_value.get("hours", 0)
        return from_time + timedelta(seconds=seconds, minutes=minutes, hours=hours)

    elif schedule_type == "interval":
        seconds = schedule_value.get("seconds", 0)
        minutes = schedule_value.get("minutes", 0)
        hours = schedule_value.get("hours", 0)
        days = schedule_value.get("days", 0)
        return from_time + timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)

    elif schedule_type == "daily":
        target_hour = schedule_value["hour"]
        target_min = schedule_value["minute"]
        day_filter = schedule_value.get("days")  # None = every day, list = specific days
        
        candidate = from_time.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
        
        for _ in range(8):  # Try up to 8 days ahead
            if day_filter is not None and candidate.weekday() not in day_filter:
                candidate += timedelta(days=1)
                continue
            if candidate > from_time:
                return candidate
            candidate += timedelta(days=1)
            
        # Fallback: just next day at that time
        return from_time.replace(hour=target_hour, minute=target_min, second=0, microsecond=0) + timedelta(days=1)

    elif schedule_type == "weekly":
        target_hour = schedule_value["hour"]
        target_min = schedule_value["minute"]
        target_days = schedule_value.get("days", [from_time.weekday()])
        
        candidate = from_time.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
        
        for _ in range(14):  # Try up to 14 days ahead
            if candidate.weekday() in target_days and candidate > from_time:
                return candidate
            candidate += timedelta(days=1)
            
        # Fallback: same time tomorrow
        return from_time + timedelta(days=1)

    raise ValueError(f"unknown schedule type: {schedule_type}")


def format_schedule_description(schedule_type, schedule_value) -> str:
    """Human-readable description of a schedule."""
    if schedule_type == "one_shot":
        secs = schedule_value.get("seconds", 0)
        mins = schedule_value.get("minutes", 0)
        hrs = schedule_value.get("hours", 0)
        parts = []
        if hrs: parts.append(f"{hrs}h")
        if mins: parts.append(f"{mins}m")
        if secs: parts.append(f"{secs}s")
        return f"in {' '.join(parts)}"
    elif schedule_type == "interval":
        secs = schedule_value.get("seconds", 0)
        mins = schedule_value.get("minutes", 0)
        hrs = schedule_value.get("hours", 0)
        days = schedule_value.get("days", 0)
        parts = []
        if days: parts.append(f"{days} day{'s' if days>1 else ''}")
        if hrs: parts.append(f"{hrs} hour{'s' if hrs>1 else ''}")
        if mins: parts.append(f"{mins} min{'s' if mins>1 else ''}")
        if secs: parts.append(f"{secs} sec{'s' if secs>1 else ''}")
        return f"every {' '.join(parts)}" if parts else "?"
    elif schedule_type == "daily":
        h = schedule_value["hour"]
        m = schedule_value["minute"]
        ampm = "am" if h < 12 else "pm"
        display_h = h if h <= 12 else h - 12
        if display_h == 0: display_h = 12
        days = schedule_value.get("days")
        if days == [0, 1, 2, 3, 4]:
            return f"weekdays at {display_h}:{m:02d} {ampm}"
        return f"daily at {display_h}:{m:02d} {ampm}"
    elif schedule_type == "weekly":
        h = schedule_value["hour"]
        m = schedule_value["minute"]
        ampm = "am" if h < 12 else "pm"
        display_h = h if h <= 12 else h - 12
        if display_h == 0: display_h = 12
        day_names = [list(_DAYS.keys())[list(_DAYS.values()).index(d)] for d in schedule_value.get("days", [])]
        return f"every {', '.join(d.title() for d in day_names)} at {display_h}:{m:02d} {ampm}" if day_names else f"weekly at {display_h}:{m:02d} {ampm}"
    return "?"


# ── TASK CRUD ──────────────────────────────────────────────────────

def _get_profile():
    try:
        import profiles
        return profiles.get_active_profile()
    except Exception:
        return "cherry"


def add_task(name, schedule_text, steps, description="", profile=None):
    """Add a new scheduled task. Returns (ok, message)."""
    if not name or not name.strip():
        return False, "task needs a name"
    if not steps or not isinstance(steps, list):
        return False, "task needs at least one step"
    if profile is None:
        profile = _get_profile()

    # Parse schedule
    try:
        sched_type, sched_val = parse_schedule(schedule_text)
    except ValueError as e:
        return False, str(e)

    # Calculate first next_run
    next_run = compute_next_run(sched_type, sched_val)
    next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")

    name = name.strip()
    with _sched_lock:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                INSERT INTO scheduled_tasks
                    (name, description, schedule_type, schedule_value, steps,
                     enabled, profile, created_at, next_run)
                VALUES (?, ?, ?, ?, ?, 1, ?, datetime('now','localtime'), ?)
                ON CONFLICT(name, profile) DO UPDATE SET
                    description = excluded.description,
                    schedule_type = excluded.schedule_type,
                    schedule_value = excluded.schedule_value,
                    steps = excluded.steps,
                    enabled = 1,
                    next_run = excluded.next_run
            """, (name, description.strip(), sched_type,
                  json.dumps(sched_val), json.dumps(steps),
                  profile, next_run_str))

    desc = format_schedule_description(sched_type, sched_val)
    print(f"[SCHED] added task '{name}' ({desc}, {len(steps)} step(s))")
    return True, f"scheduled '{name}' — {desc}"


def delete_task(name, profile=None):
    if profile is None:
        profile = _get_profile()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("DELETE FROM scheduled_tasks WHERE name=? AND profile=?",
                          (name.strip(), profile))
        return cur.rowcount > 0


def list_tasks(profile=None):
    if profile is None:
        profile = _get_profile()
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute("""
            SELECT id, name, description, schedule_type, schedule_value,
                   steps, enabled, created_at, last_run, next_run, run_count
            FROM scheduled_tasks
            WHERE profile=?
            ORDER BY next_run ASC, created_at DESC
        """, (profile,)).fetchall()

    result = []
    for r in rows:
        sched_val = json.loads(r[4]) if r[4] else {}
        result.append({
            "id": r[0], "name": r[1], "description": r[2],
            "schedule_type": r[3], "schedule_value": sched_val,
            "schedule_desc": format_schedule_description(r[3], sched_val),
            "steps": json.loads(r[5]) if r[5] else [],
            "enabled": bool(r[6]), "created_at": r[7],
            "last_run": r[8], "next_run": r[9], "run_count": r[10],
        })
    return result


def get_task(name, profile=None):
    if profile is None:
        profile = _get_profile()
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("""
            SELECT id, name, description, schedule_type, schedule_value,
                   steps, enabled, created_at, last_run, next_run, run_count
            FROM scheduled_tasks WHERE name=? AND profile=?
        """, (name.strip(), profile)).fetchone()
    if not row:
        return None
    sched_val = json.loads(row[4]) if row[4] else {}
    return {
        "id": row[0], "name": row[1], "description": row[2],
        "schedule_type": row[3], "schedule_value": sched_val,
        "schedule_desc": format_schedule_description(row[3], sched_val),
        "steps": json.loads(row[5]) if row[5] else [],
        "enabled": bool(row[6]), "created_at": row[7],
        "last_run": row[8], "next_run": row[9], "run_count": row[10],
    }


def toggle_task(name, enabled=None, profile=None):
    """Enable or disable a task. If enabled is None, toggle."""
    if profile is None:
        profile = _get_profile()
    task = get_task(name, profile)
    if not task:
        return False, f"task '{name}' not found"
    
    new_state = not task["enabled"] if enabled is None else enabled
    with sqlite3.connect(DB_PATH) as con:
        con.execute("UPDATE scheduled_tasks SET enabled=? WHERE name=? AND profile=?",
                    (1 if new_state else 0, name.strip(), profile))
    return True, f"{'enabled' if new_state else 'disabled'} task '{name}'"


# ── TASK EXECUTION ─────────────────────────────────────────────────

def execute_step(step, profile):
    """Execute a single task step. Returns (ok, result_text)."""
    step_type = step.get("type", "tool")
    
    if step_type == "tool":
        tool_name = step.get("tool", "")
        tool_args = step.get("args", {})
        if not tool_name:
            return False, "no tool name in step"
        
        # Import execute_system_tool dynamically to avoid circular imports
        from jarvis_engine import execute_system_tool
        try:
            result = execute_system_tool(tool_name, tool_args)
            return True, result
        except Exception as e:
            return False, f"tool '{tool_name}' error: {e}"
    
    elif step_type == "message":
        content = step.get("content", "")
        if not content:
            return False, "empty message content"
        return True, content
    
    return False, f"unknown step type: {step_type}"


def run_task(task, profile):
    """Execute all steps of a task. Returns list of (ok, result) per step."""
    steps = task.get("steps", [])
    results = []
    
    for step in steps:
        ok, result = execute_step(step, profile)
        results.append((ok, result))
        
        # If a tool step fails, stop the chain
        if not ok and step.get("type", "tool") == "tool":
            break
    
    return results


def update_task_run(task, profile):
    """Update last_run and compute next_run for a task.
    One-shot tasks are disabled after execution."""
    sched_type = task["schedule_type"]
    sched_val = task["schedule_value"]
    now = datetime.now()
    
    with sqlite3.connect(DB_PATH) as con:
        if sched_type == "one_shot":
            # Disable one-shot tasks after they fire
            con.execute("""
                UPDATE scheduled_tasks
                SET last_run = datetime('now','localtime'),
                    run_count = run_count + 1,
                    enabled = 0
                WHERE name=? AND profile=?
            """, (task["name"], profile))
        else:
            next_run = compute_next_run(sched_type, sched_val, now)
            next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
            con.execute("""
                UPDATE scheduled_tasks
                SET last_run = datetime('now','localtime'),
                    next_run = ?,
                    run_count = run_count + 1
                WHERE name=? AND profile=?
            """, (next_run_str, task["name"], profile))


# ── BACKGROUND SCHEDULER LOOP ──────────────────────────────────────

def _scheduler_loop():
    """Background thread that checks for due tasks every 30 seconds."""
    while not _sched_stop.is_set():
        try:
            profile = _get_profile()
            now = datetime.now()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            
            with _sched_lock:
                with sqlite3.connect(DB_PATH) as con:
                    rows = con.execute("""
                        SELECT name, schedule_type, schedule_value, steps
                        FROM scheduled_tasks
                        WHERE enabled=1 AND profile=? AND next_run IS NOT NULL
                          AND next_run <= ?
                        ORDER BY next_run ASC
                    """, (profile, now_str)).fetchall()
            
            for row in rows:
                task = get_task(row[0], profile)
                if not task:
                    continue
                
                print(f"[SCHED] executing task '{task['name']}'")
                results = run_task(task, profile)
                
                # Push results via callback if set
                if _sched_callback:
                    for ok, result in results:
                        msg = result
                        if ok:
                            # Push result to UI for both recurring and one-shot tasks
                            prefix = "⏰" if task["schedule_type"] != "one_shot" else "⏱️"
                            display = f"{prefix} {task['name']}: {result}" if result else f"{prefix} {task['name']} done!!"
                            _sched_callback(display)
                
                # Update run tracking
                update_task_run(task, profile)
        
        except Exception as e:
            print(f"[SCHED] loop error: {e}")
        
        _sched_stop.wait(30)


def set_scheduler_callback(fn):
    """Set the callback function for pushing scheduled results to UI."""
    global _sched_callback
    _sched_callback = fn


def start_scheduler():
    """Start the background scheduler thread."""
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    print("[SCHED] scheduler thread started (30s check interval)")
    return thread


def stop_scheduler():
    """Signal the scheduler thread to stop."""
    _sched_stop.set()
    print("[SCHED] scheduler stop signal sent")


# ── WORKFLOW HELPERS ──────────────────────────────────────────────

def make_step_tool(tool_name, **kwargs):
    """Helper to create a tool step."""
    return {"type": "tool", "tool": tool_name, "args": kwargs}


def make_step_message(content):
    """Helper to create a message step."""
    return {"type": "message", "content": content}


# Tool list for jarvis_engine.py integration
SCHEDULER_TOOL_LIST = (
    "\n"
    "SCHEDULED TASK TOOLS:\n"
    "[CALL_TOOL: add_task, name=check weather, schedule=every morning at 9am, action=check weather in Tokyo]  ← schedule tasks in plain English\n"
    "[CALL_TOOL: list_tasks]\n"
    "[CALL_TOOL: delete_task, name=check weather]  ← name must match exactly\n"
    "[CALL_TOOL: toggle_task, name=check weather, enabled=true]\n"
)
