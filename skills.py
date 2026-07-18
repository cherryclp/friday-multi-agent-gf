# skills.py
# Jarvis Skill Learning System

import sqlite3
import json
import os
import threading
import time

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(JARVIS_DIR, "jarvis_memory.db")
_skill_lock = threading.Lock()
_recent_interactions = []


def _init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                trigger_phrases TEXT NOT NULL DEFAULT '[]',
                steps TEXT NOT NULL DEFAULT '[]',
                usage_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                last_used TEXT,
                profile TEXT DEFAULT 'cherry',
                UNIQUE(name, profile)
            );
        """)

_init_db()


def _get_profile():
    try:
        import profiles
        return profiles.get_active_profile()
    except Exception:
        return "cherry"


def learn_skill(name, description, trigger_phrases, steps, profile=None):
    if not name or not name.strip():
        return False, "skill name can't be empty"
    if not steps or not isinstance(steps, list):
        return False, "skill needs steps"
    name = name.strip().lower().replace(" ", "_")
    if profile is None:
        profile = _get_profile()
    with _skill_lock:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("""
                INSERT INTO skills (name, description, trigger_phrases, steps, profile, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
                ON CONFLICT(name, profile) DO UPDATE SET
                    description = excluded.description,
                    trigger_phrases = excluded.trigger_phrases,
                    steps = excluded.steps
            """, (name, description.strip(), json.dumps(trigger_phrases),
                  json.dumps(steps), profile))
    print(f"[SKILLS] learned '{name}' ({len(steps)} steps)")
    return True, f"learned skill '{name}'!! ({len(steps)} steps)"


def delete_skill(name, profile=None):
    if profile is None:
        profile = _get_profile()
    name = name.strip().lower().replace(" ", "_")
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("DELETE FROM skills WHERE name=? AND profile=?", (name, profile))
        return cur.rowcount > 0


def list_skills(profile=None):
    if profile is None:
        profile = _get_profile()
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(
            "SELECT id, name, description, trigger_phrases, usage_count, created_at, last_used "
            "FROM skills WHERE profile=? ORDER BY usage_count DESC, created_at DESC",
            (profile,)).fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "name": r[1], "description": r[2],
            "trigger_phrases": json.loads(r[3]) if r[3] else [],
            "usage_count": r[4], "created_at": r[5], "last_used": r[6],
        })
    return result


def get_skill(name, profile=None):
    if profile is None:
        profile = _get_profile()
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT id, name, description, trigger_phrases, steps, usage_count "
            "FROM skills WHERE name=? AND profile=?",
            (name.strip().lower().replace(" ", "_"), profile)).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "name": row[1], "description": row[2],
        "trigger_phrases": json.loads(row[3]) if row[3] else [],
        "steps": json.loads(row[4]) if row[4] else [],
        "usage_count": row[5],
    }


def find_matching_skill(user_message, profile=None):
    if profile is None:
        profile = _get_profile()
    msg_lower = user_message.lower().strip()
    all_skills = list_skills(profile)
    if not all_skills:
        return None
    best_match = None
    best_score = 0
    for skill in all_skills:
        for phrase in skill["trigger_phrases"]:
            p = phrase.lower().strip()
            if msg_lower == p:
                return skill
            if p in msg_lower:
                if len(p) > best_score:
                    best_score = len(p)
                    best_match = skill
            if msg_lower.startswith(p) or p.startswith(msg_lower):
                score = min(len(p), len(msg_lower))
                if score > best_score:
                    best_score = score
                    best_match = skill
    return best_match if best_score > 3 else None


def record_interaction(user_message, tool_calls, reply):
    global _recent_interactions
    if not tool_calls:
        return
    with _skill_lock:
        _recent_interactions.append({
            "user_message": user_message,
            "tool_calls": tool_calls,
            "reply": reply,
            "timestamp": time.time(),
        })
        if len(_recent_interactions) > 8:
            _recent_interactions = _recent_interactions[-8:]


def get_recent_interactions(count=3):
    with _skill_lock:
        return _recent_interactions[-count:] if _recent_interactions else []


def clear_recent_interactions():
    global _recent_interactions
    with _skill_lock:
        _recent_interactions = []


def should_auto_learn(min_interactions=2):
    with _skill_lock:
        return len(_recent_interactions) >= min_interactions


def auto_extract_skill(llm, interactions):
    if not interactions:
        return None
    lines = []
    for ix, ia in enumerate(interactions):
        lines.append(f"--- Exchange {ix+1} ---")
        lines.append(f"User said: {ia['user_message']}")
        for tool_name, args, result in ia["tool_calls"]:
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            lines.append(f"  Used tool: {tool_name}({args_str})")
            lines.append(f"  Result: {str(result)[:150]}")
        lines.append(f"Jarvis replied: {ia['reply'][:200]}")
    prompt = (
        "You are a skill-learning system that extracts reusable skills from successful multi-step interactions.\n\n"
        + "\n".join(lines)
        + "\n\nAnalyze these interactions and extract a reusable skill capturing the COMMON workflow pattern.\n\n"
        "Respond ONLY with valid JSON in this exact format:\n"
        "{\n"
        '  "name": "short_snake_case_name",\n'
        '  "description": "brief description of what this skill does",\n'
        '  "trigger_phrases": ["phrase to invoke", "another way to say it", "third variant"],\n'
        '  "steps": [\n'
        '    {"tool": "tool_name", "args": {"arg1": "value1"}}\n'
        "  ]\n"
        "}\n\n"
        "RULES:\n"
        "- name: Use snake_case, max 3 words. Describe the action (e.g. open_youtube_and_search, check_weather).\n"
        "- description: One short sentence explaining what this skill does.\n"
        "- trigger_phrases: EXACTLY 3-5 natural phrases users would say. Include varied phrasings. Be specific.\n"
        "  Example for a weather skill: ['weather', 'how hot is it outside', 'check temperature', 'what is the temp', 'forecast']\n"
        "- steps: The exact tool calls needed to perform this skill from ALL exchanges, deduplicated.\n"
        "  Include each unique tool call only once, in the order they should execute.\n"
        "  Use consistent arg values that would work every time the skill is invoked.\n"
        "  If the same tool appears across multiple exchanges with different args, use the most general version.\n"
        "- Keep steps minimal: only include the essential tool calls, remove duplicates.\n"
        "- If no clear multi-step pattern emerges across the exchanges, return empty steps []."
    )
    try:
        resp = llm.invoke([("system", prompt)]).content.strip()
        for f in ["```json", "```"]:
            if f in resp:
                parts = resp.split(f)
                if len(parts) >= 2:
                    resp = parts[1]
                    if "```" in resp:
                        resp = resp.split("```")[0]
                    break
        data = json.loads(resp.strip())
        if data.get("name") and data.get("trigger_phrases"):
            return data
    except Exception as e:
        print(f"[SKILLS] auto-extract error: {e}")
    return None


def increment_usage(name, profile=None):
    if profile is None:
        profile = _get_profile()
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "UPDATE skills SET usage_count = usage_count + 1, "
            "last_used = datetime('now','localtime') "
            "WHERE name=? AND profile=?",
            (name.strip().lower().replace(" ", "_"), profile))


SKILL_TOOL_LIST = (
    "\n"
    "SKILL TOOLS:\n"
    "[CALL_TOOL: list_skills]\n"
    "[CALL_TOOL: delete_skill, name=skill_name]\n"
)
