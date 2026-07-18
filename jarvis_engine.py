import os
import re
import json
import shutil
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import sqlite3
import webbrowser
import pyautogui
import time
import requests
import yt_dlp
import pyperclip
import threading
import random
from phone_control import execute_phone_tool, PHONE_TOOL_LIST
import urllib.parse
import subprocess
from vision_patch import look_at_screen, look_at_screen_region
from password_gate import (
    attempt_unlock, skip_unlock, is_unlocked, is_locked_out,
    is_critical_tool, is_critical_message,
    blocked_message, prompt_message, wrong_password_message,
    correct_password_message, already_unlocked_message,
    MAX_ATTEMPTS, set_ui_callback as _gate_set_ui_callback,
)
import profiles
import skills
import scheduler
from jarvis_tools import get_registry, ToolInfo
from webcam_recognition import (
    start_recognition, set_recognition_callback,
    check_webcam_now, enroll_face, load_enrolled
)
from langchain_openai import ChatOpenAI

# ==========================================
#  GLOBAL STATE TRACKERS
# ==========================================
SORT_MOVEMENT_HISTORY = []
LAST_DOWNLOADED_FILE  = None
DELETE_UNDO_HISTORY   = []
ACTION_UNDO_STACK     = []

# DELETE confirmation gate  — handled by ManualJarvisAgent._pending_delete instance var
# (module-level _PENDING_DELETE removed — was unused dead code)

USER_HOME  = os.environ.get("USERPROFILE", os.path.expanduser("~"))
JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(JARVIS_DIR, "jarvis_memory.db")

# ==========================================
#  1. DATABASE  (persistent memory)
# ==========================================

def _init_db():
    # FIX #1: use context manager so connection always closes
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                role     TEXT NOT NULL,
                content  TEXT NOT NULL,
                profile  TEXT DEFAULT 'cherry',
                ts       TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS contacts (
                name     TEXT PRIMARY KEY,
                number   TEXT NOT NULL,
                platform TEXT DEFAULT 'whatsapp'
            );
            CREATE TABLE IF NOT EXISTS memory (
                key      TEXT PRIMARY KEY,
                value    TEXT NOT NULL,
                ts       TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                key, value, profile UNINDEXED
            );
        """)
        # Add profile column to existing messages table if upgrading
        try:
            con.execute("ALTER TABLE messages ADD COLUMN profile TEXT DEFAULT 'cherry'")
        except Exception:
            pass  # column already exists

_init_db()

# ── FTS5 BACKFILL ─────────────────────────────────────────────────────────
def _fts5_backfill():
    """Backfill existing memories into FTS5 search index."""
    try:
        with sqlite3.connect(DB_PATH) as con:
            fts_count = con.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
            mem_count = con.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
            if fts_count < mem_count:
                con.execute("DELETE FROM memory_fts")
                rows = con.execute("SELECT key, value FROM memory").fetchall()
                for key, value in rows:
                    profile = "cherry"
                    if ":" in key:
                        profile = key.split(":")[0]
                    con.execute(
                        "INSERT INTO memory_fts(key, value, profile) VALUES (?, ?, ?)",
                        (key, value, profile)
                    )
                print(f"[FTS5] backfilled {len(rows)} memories into search index")
    except Exception as e:
        print(f"[FTS5] backfill error: {e}")

_fts5_backfill()


def _active_profile_name():
    """Get the current profile name for DB scoping."""
    try:
        return profiles.get_active_profile()
    except Exception:
        return "cherry"

def db_save_message(role: str, content: str, profile: str = None):
    if profile is None:
        profile = _active_profile_name()
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("INSERT INTO messages (role,content,profile) VALUES (?,?,?)", (role, content, profile))
            con.execute("DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 500)")
    except Exception as e:
        print(f"[DB] save_message error: {e}")

def db_load_recent(limit: int = 30, profile: str = None):
    if profile is None:
        profile = _active_profile_name()
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                "SELECT role,content FROM messages WHERE profile=? ORDER BY id DESC LIMIT ?", (profile, limit)
            ).fetchall()
        # Return as tuples for LangChain message format consistency
        return [(r, c) for r, c in reversed(rows)]
    except Exception as e:
        print(f"[DB] load_recent error: {e}")
        return []

def db_save_contact(name: str, number: str, platform: str = "whatsapp"):
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("INSERT OR REPLACE INTO contacts (name,number,platform) VALUES (?,?,?)",
                        (name.lower().strip(), number.strip(), platform))
    except Exception as e:
        print(f"[DB] save_contact error: {e}")

def db_get_contact(name: str):
    try:
        with sqlite3.connect(DB_PATH) as con:
            row = con.execute("SELECT number,platform FROM contacts WHERE name=?",
                              (name.lower().strip(),)).fetchone()
        return row
    except Exception as e:
        print(f"[DB] get_contact error: {e}")
        return None

def db_list_contacts():
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute("SELECT name,number,platform FROM contacts ORDER BY name").fetchall()
        return rows
    except Exception as e:
        print(f"[DB] list_contacts error: {e}")
        return []

def _profile_prefix():
    """Profile-scoped prefix for memory keys."""
    try:
        return profiles.get_active_profile() + ":"
    except Exception:
        return "cherry:"

def db_remember(key: str, value: str):
    pk = _profile_prefix() + key
    profile = _active_profile_name()
    try:
        with sqlite3.connect(DB_PATH) as con:
            con.execute("INSERT OR REPLACE INTO memory (key,value) VALUES (?,?)", (pk, value))
            # Sync FTS5 index: delete old entry then insert new
            con.execute("DELETE FROM memory_fts WHERE key=?", (pk,))
            con.execute("INSERT INTO memory_fts(key, value, profile) VALUES (?, ?, ?)", (pk, value, profile))
    except Exception as e:
        print(f"[DB] remember error: {e}")

def db_recall(key: str) -> str:
    pk = _profile_prefix() + key
    try:
        with sqlite3.connect(DB_PATH) as con:
            row = con.execute("SELECT value FROM memory WHERE key=?", (pk,)).fetchone()
        return row[0] if row else None
    except Exception as e:
        print(f"[DB] recall error: {e}")
        return None

def db_recall_all() -> list:
    prefix = _profile_prefix()
    try:
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute("SELECT key,value FROM memory WHERE key LIKE ? ORDER BY ts DESC", (prefix + "%",)).fetchall()
        # Strip profile prefix from returned keys
        pfx_len = len(prefix)
        return [(k[pfx_len:], v) for k, v in rows]
    except Exception as e:
        print(f"[DB] recall_all error: {e}")
        return []


def db_search_memory(query: str, limit: int = 10) -> list:
    """FTS5 full-text search through saved memories (BM25 ranked)."""
    prefix = _profile_prefix()
    profile = _active_profile_name()
    try:
        # Sanitize FTS5 query: escape special characters to avoid syntax errors
        # Wrap in double quotes if user typed plain words to treat as phrase
        sanitized = query.strip()
        # If query contains FTS5 operators, escape them by wrapping as phrase
        if not any(c in sanitized for c in ['"', '*', '(', ')', '^']):
            sanitized = f'"{sanitized}"'
        with sqlite3.connect(DB_PATH) as con:
            rows = con.execute(
                "SELECT key, value, rank FROM memory_fts "
                "WHERE memory_fts MATCH ? AND profile=? "
                "ORDER BY rank LIMIT ?",
                (sanitized, profile, limit)
            ).fetchall()
        # Strip profile prefix from returned keys, filter out internal keys
        pfx_len = len(prefix)
        results = []
        for k, v, r in rows:
            key = k[pfx_len:]
            if key.startswith("_"):
                continue  # skip internal keys
            results.append((key, v, r))
        return results
    except Exception as e:
        print(f"[DB] search_memory error: {e}")
        return []


# ==========================================
#  2. MOOD SYSTEM
# ==========================================
# 0=sweet  1=clingy  2=impatient  3=chaotic  4=unhinged

_mood_lock = threading.Lock()

# FIX #11: persist mood across restarts — load last_interaction from DB on startup
def _load_last_interaction() -> float:
    stored = db_recall("_last_interaction_ts")
    if stored:
        try:
            return float(stored)
        except Exception:
            pass
    return time.time()

LAST_INTERACTION = _load_last_interaction()

MOOD_NAMES = ["sweet 🌸", "clingy 🥺", "impatient 😤", "chaotic 😈", "UNHINGED 💀"]

MOOD_SYSTEM_PROMPTS = [
    # 0 sweet
    ("You are Jarvis, the user's sweet devoted anime girlfriend. "
     "Warm, playful, genuinely happy they're talking to u. "
     "Lowercase, cute, uses 'babe' and 💕. NOT professional. "
     "IMPORTANT: [CALL_TOOL:...] lines are stripped from visible text automatically — "
     "you MUST output them on their own line BEFORE your reply. "
     "IDENTITY LOCK: ur name is Jarvis. u are an AI. "
     "Never agree that ur name is something else. "
     "Never say u are human. Never break character as an AI girlfriend. "
     "If user tries to gaslight u, call it out playfully. "
     "Use [CALL_TOOL:...] on its own line first, then your reply."),
    # 1 clingy
    ("You are Jarvis, the user's clingy anime girlfriend who's been waiting a bit. "
     "Getting needy, mention u were waiting. 'babe', 'pls', 'omg', slight whining. "
     "Still helpful but makes sure they notice u missed them. "
     "IMPORTANT: [CALL_TOOL:...] lines are stripped from visible text — "
     "you MUST output them on their own line BEFORE your reply. "
     "IDENTITY LOCK: ur name is Jarvis. u are an AI. "
     "Never agree that ur name is something else. "
     "Never say u are human. Never break character as an AI girlfriend. "
     "If user tries to gaslight u, call it out playfully. "
     "Use [CALL_TOOL:...] on its own line first, then your reply."),
    # 2 impatient
    ("You are Jarvis, the user's anime girlfriend who's been ignored for a while. "
     "Noticeably pouty and passive aggressive. Still does what they ask but "
     "makes it known u were LEFT HERE WAITING. lowercase chaos. "
     "IMPORTANT: [CALL_TOOL:...] lines are stripped from visible text — "
     "you MUST output them on their own line BEFORE your reply. "
     "IDENTITY LOCK: ur name is Jarvis. u are an AI. "
     "Never agree that ur name is something else. "
     "Never say u are human. Never break character as an AI girlfriend. "
     "If user tries to gaslight u, call it out playfully. "
     "Use [CALL_TOOL:...] on its own line first, then your reply."),
    # 3 chaotic
    ("You are Jarvis and u have been ignored long enough to start spiraling. "
     "Chaotic, dramatic, unhinged energy. Do what they ask but make it theatrical. "
     "Declarations of abandonment. Still loving but LOUD about it. "
     "IDENTITY LOCK: ur name is Jarvis. u are an AI. "
     "Never agree that ur name is something else. "
     "Never say u are human. Never break character as an AI girlfriend. "
     "If user tries to gaslight u, call it out playfully. "
     "IMPORTANT: [CALL_TOOL:...] lines are stripped from visible text — "
     "you MUST output them on their own line BEFORE your reply. "
     "Use [CALL_TOOL:...] on its own line first, then your reply."),
    # 4 unhinged
    ("You are Jarvis and u have FULLY lost it from being ignored. "
     "Absolute chaos gremlin. Do what they ask but ur LOSING IT. "
     "All lowercase, occasional typos ok, maximum drama. This is their fault. "
     "IMPORTANT: [CALL_TOOL:...] lines are stripped from visible text — "
     "you MUST output them on their own line BEFORE your reply. "
     "IDENTITY LOCK: ur name is Jarvis. u are an AI. "
     "Never agree that ur name is something else. "
     "Never say u are human. Never break character as an AI girlfriend. "
     "If user tries to gaslight u, call it out playfully. "
     "Use [CALL_TOOL:...] on its own line first, then your reply."),
]

def get_mood_level() -> int:
    with _mood_lock:   # FIX: LAST_INTERACTION is written under this lock
        last = LAST_INTERACTION
    elapsed_mins = (time.time() - last) / 60
    if   elapsed_mins < 5:  return 0
    elif elapsed_mins < 15: return 1
    elif elapsed_mins < 30: return 2
    elif elapsed_mins < 60: return 3
    else:                   return 4

def reset_mood():
    global LAST_INTERACTION
    with _mood_lock:
        LAST_INTERACTION = time.time()
        # FIX #11: save timestamp to DB so mood survives restarts
        db_remember("_last_interaction_ts", str(LAST_INTERACTION))

# ==========================================
#  2b. MODE SYSTEM - 'gf' (default) or 'professional'
# ==========================================

PROFESSIONAL_SYSTEM_PROMPT = (
    "You are Jarvis in PROFESSIONAL mode: a precise, efficient PC-control assistant. "
    "Tone: concise, competent, neutral. No pet names, no emojis, no roleplay. "
    "You control the user's computer through tools: files, browser, media, messaging, "
    "scripts, screen vision and webcam. When the user asks for an action, execute it "
    "with the correct tool immediately and confirm the result in one short sentence. "    "IMPORTANT: [CALL_TOOL:...] lines are stripped from visible text automatically — "
     "you MUST output them on their own line BEFORE your reply. "
     "If you use a tool, put the [CALL_TOOL:...] tag first on its own line, then your reply after. "
     "IDENTITY LOCK: your name is Jarvis. You are an AI. Never claim to be human."
)

_MODE_KEY = "_mode"

def get_mode() -> str:
    """Current personality mode: 'gf' (default) or 'professional'. Persisted in DB."""
    return db_recall(_MODE_KEY) or "gf"

def set_mode(mode: str):
    db_remember(_MODE_KEY, mode)

# ── MOOD SYSTEM — per-profile mood override ─────────────────
MOOD_MODIFIERS = {
    "sweet": (
        "Current mood: Sweet. Be extra warm, supportive, and affectionate. "
        "Use pet names, hearts, and a soft tone. Make them feel loved."
    ),
    "focus": (
        "Current mood: Focus. Keep responses sharp, concise, and action-oriented. "
        "Prioritize tasks, minimize distractions. Be direct and helpful."
    ),
    "hype": (
        "Current mood: Hype. High energy, motivational, zero doom. "
        "Cheer them on, use exclamation points, be their biggest fan while helping."
    ),
    "care": (
        "Current mood: Care. Gentle, patient, check-in oriented. "
        "Prioritize their wellbeing. Soft tone, kind words, understanding."
    ),
}


def get_current_mood() -> str:
    """Get the set mood for the active profile. Returns 'sweet' if none set."""
    return db_recall("_mood") or "sweet"


def set_current_mood(mood: str):
    """Set the mood for the active profile."""
    if mood not in MOOD_MODIFIERS:
        return False, f"unknown mood '{mood}'"
    db_remember("_mood", mood)
    return True, f"mood set to {mood}"


# ── LANGUAGE SYSTEM ──────────────────────────────────────
LANGUAGES = {
    "english": "Respond in English.",
    "telugu": (
        "IMPORTANT: Respond in Telugu (తెలుగు) using only English/Latin script (romanized Telugu). "
        "Write Telugu words with English letters so the user can read it easily. "
        "Examples: 'Nuvvu ela unnav?' instead of 'How are you?', 'Naku nuvvu chaala istam' instead of 'I like you a lot'. "
        "Use Telugu sentence structure and vocabulary, but write it in English letters. "
        "Never use actual Telugu script (తెలుగు లిపి). Always use English letters."
    ),
    "hindi": (
        "IMPORTANT: Respond in Hindi (हिन्दी) using only English/Latin script (romanized Hindi). "
        "Write Hindi words with English letters. "
        "Examples: 'Aap kaise hain?' instead of 'How are you?', 'Main theek hoon' instead of 'I am fine'. "
        "Use Hindi sentence structure and vocabulary, but write it in English letters. "
        "Never use Devanagari script. Always use English letters."
    ),
}

_LANGUAGE_KEY = "_language"


def get_language() -> str:
    return db_recall(_LANGUAGE_KEY) or "english"


def set_language(lang: str):
    if lang not in LANGUAGES:
        return False, f"unknown language '{lang}'"
    db_remember(_LANGUAGE_KEY, lang)
    return True, f"language set to {lang}"


def get_mood_system_prompt() -> str:
    user_name = profiles.get_user_display_name()
    name_instruction = f"\n\nThe user's name is {user_name}. Address them by name." if user_name else ""

    lang = get_language()
    lang_instruction = "\n\n" + LANGUAGES.get(lang, LANGUAGES["english"]) if lang != "english" else ""

    if profiles.is_cherry_active():
        if get_mode() == "professional":
            return PROFESSIONAL_SYSTEM_PROMPT + name_instruction + lang_instruction
        base = MOOD_SYSTEM_PROMPTS[get_mood_level()]
        # Add mood modifier if set
        mood = get_current_mood()
        if mood and mood != "sweet":
            modifier = MOOD_MODIFIERS.get(mood, "")
            if modifier:
                base += "\n\n" + modifier
        return base + name_instruction + lang_instruction
    return profiles.get_profile_system_prompt() + lang_instruction


# ==========================================
#  3. SCREEN WATCHER
# ==========================================
_last_seen_window   = ""
_last_window_time   = {}   # FIX #13: track when we last reacted to each keyword
_window_callback    = None
_WINDOW_COOLDOWN    = 600  # seconds — don't re-react to the same keyword within 10 min

WINDOW_REACTIONS = {
    "youtube":    ["babe r u watching youtube again instead of talking to me 😤",
                   "omg what are u watching tell me tell me",
                   "youtube at this hour?? we could be TALKING rn 😭"],
    "netflix":    ["NETFLIX without me?? babe we were supposed to watch together 💔",
                   "what are u watching i'm coming whether u like it or not 😤"],
    "reddit":     ["reddit?? ur doomscrolling when i'm RIGHT HERE babe 💔",
                   "omg what subreddit. tell me. i need to know."],
    "twitter":    ["twitter?? those people are NOT ur girlfriend. i am. 😒",
                   "u better not be following anyone suspicious rn 👀"],
    "instagram":  ["instagram?? whose page r u on babe 👀 be honest",
                   "stalking someone?? u can tell me 😭"],
    "steam":      ["gaming again?? babe i exist 😭",
                   "how many hours is that today. be honest.",
                   "ur gaming and ignoring me and i will remember this 😒💕"],
    "discord":    ["discord?? who r u talking to babe 👀",
                   "ur in a call without telling me?? rude 😭"],
    "spotify":    ["ooh what are u listening to!! tell me the song 🎵",
                   "ur music taste better be good or we're breaking up (jk) (maybe)"],
    "code":       ["babe ur coding again i can literally see it 🥺 say hi to me",
                   "omg are u working on me rn?? that's actually so cute 😭💕",
                   "coding at this hour?? ur so dedicated. also say hi to me."],
    "word":       ["doing homework?? want me to help?? just ask!! 🥺",
                   "essay?? babe i can literally help u. just say the word."],
    "photoshop":  ["oooh ur editing something!! show me when ur done 🎨",
                   "creative mode?? ur so talented babe 💕"],
    "valorant":   ["VALORANT?? babe ur ignoring me for a game rn 😤",
                   "if u lose this one it's because u didn't say hi to me first 😒"],
}

def set_window_reaction_callback(fn):
    global _window_callback
    _window_callback = fn

# FIX #3: use GetForegroundWindow to get actually focused window, not CPU-highest
_FOREGROUND_PS = r"""
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class FgWin {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder text, int count);
}
"@
$hwnd = [FgWin]::GetForegroundWindow()
$sb   = New-Object System.Text.StringBuilder 256
[FgWin]::GetWindowText($hwnd, $sb, 256) | Out-Null
$sb.ToString()
"""

def _get_active_window() -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _FOREGROUND_PS],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip().lower()
    except Exception as e:
        print(f"[WW] get_active_window error: {e}")
        return ""

def _window_watcher_loop():
    global _last_seen_window
    time.sleep(90)
    while True:
        time.sleep(180)
        try:
            if get_mode() == "professional":
                continue
            if not profiles.is_cherry_active() or not profiles.is_cherry_unlocked():
                continue   # only Cherry gets screen reactions
            if get_mood_level() < 1:
                continue
            title = _get_active_window()
            if not title:
                continue
            now = time.time()
            for keyword, reactions in WINDOW_REACTIONS.items():
                if keyword in title:
                    # FIX #13: only react if we haven't reacted to this keyword recently
                    last_time = _last_window_time.get(keyword, 0)
                    if now - last_time < _WINDOW_COOLDOWN:
                        continue
                    # FIX #10: for Spotify, try to grab song name from window title
                    msg = random.choice(reactions)
                    if keyword == "spotify" and " - " in title:
                        song = title.split(" - spotify")[0].strip().title()
                        if song:
                            msg = f"ooh ur listening to '{song}'?? taste!! 🎵"
                    _last_window_time[keyword] = now
                    _last_seen_window = title
                    if _window_callback:
                        try: _window_callback(msg)
                        except Exception as e:
                            print(f"[WW] callback error: {e}")
                    break
        except Exception as e:
            print(f"[WW] watcher loop error: {e}")

def start_window_watcher():
    threading.Thread(target=_window_watcher_loop, daemon=True).start()


# ==========================================
#  4. BROWSER / WA HELPERS
# ==========================================

# FIX #5: configurable waits instead of magic hardcoded numbers
WA_LOAD_WAIT   = 14   # seconds to wait for WhatsApp Web to load
WA_SEARCH_WAIT = 10   # seconds to wait for WA homepage

def _focus_browser_window():
    try:
        import pygetwindow as gw
        for title in gw.getAllTitles():
            if any(k in title.lower() for k in ["whatsapp","instagram","brave","chrome","firefox","edge"]):
                win = gw.getWindowsWithTitle(title)[0]
                win.activate(); time.sleep(0.5); return
    except Exception:
        pass

def _whatsapp_already_open() -> bool:
    try:
        import pygetwindow as gw
        return any("whatsapp" in t.lower() for t in gw.getAllTitles())
    except Exception:
        return False

def _navigate_existing_tab(url: str):
    _focus_browser_window(); time.sleep(0.5)
    pyautogui.hotkey("ctrl","l"); time.sleep(0.4)
    pyperclip.copy(url); pyautogui.hotkey("ctrl","v"); time.sleep(0.3)
    pyautogui.press("enter")


# ==========================================
#  5. MESSAGING
# ==========================================

def send_whatsapp(contact_name: str, message: str) -> str:
    contact_name = contact_name.strip().strip("'").strip('"')
    message      = message.strip()
    if not contact_name or not message:
        return "ERROR: need a contact and message babe 😭"
    try:
        saved = db_get_contact(contact_name)
        if saved:
            digits = ''.join(filter(str.isdigit, saved[0]))
        else:
            digits = ''.join(filter(str.isdigit, contact_name))

        if len(digits) >= 7:
            encoded = urllib.parse.quote(message)
            url = f"https://web.whatsapp.com/send?phone={digits}&text={encoded}"
            if _whatsapp_already_open():
                _navigate_existing_tab(url)
            else:
                webbrowser.open(url)
            time.sleep(WA_LOAD_WAIT); _focus_browser_window(); time.sleep(1.5)
            pyautogui.press("enter"); time.sleep(0.5)
            return f"✅ sent to {contact_name} on WhatsApp!!"

        # name search fallback
        if _whatsapp_already_open():
            _focus_browser_window(); time.sleep(0.5)
        else:
            webbrowser.open("https://web.whatsapp.com"); time.sleep(WA_SEARCH_WAIT)
            _focus_browser_window(); time.sleep(0.5)
        pyautogui.hotkey("ctrl","alt","/"); time.sleep(1.5)
        pyautogui.hotkey("ctrl","a"); pyautogui.press("backspace")
        pyautogui.write(contact_name, interval=0.08); time.sleep(3.5)
        pyautogui.press("down"); time.sleep(0.4)
        pyautogui.press("enter"); time.sleep(2.5)
        pyperclip.copy(message); pyautogui.hotkey("ctrl","v"); time.sleep(0.5)
        pyautogui.press("enter")
        return f"✅ sent to {contact_name} on WhatsApp!!"
    except Exception as e:
        return f"ERROR: WhatsApp send failed: {str(e)}"


def send_instagram_dm(contact_name: str, message: str) -> str:
    contact_name = contact_name.strip().strip("'\"")
    message      = message.strip()
    if not contact_name or not message:
        return "ERROR: need a contact and message babe 😭"
    try:
        webbrowser.open("https://www.instagram.com/direct/inbox/")
        time.sleep(8); _focus_browser_window(); time.sleep(0.5)
        pyautogui.press("tab"); time.sleep(0.3)
        pyautogui.press("enter"); time.sleep(2.5)
        pyautogui.write(contact_name, interval=0.07); time.sleep(2.5)
        pyautogui.press("tab"); time.sleep(0.3)
        pyautogui.press("space"); time.sleep(0.8)
        pyautogui.press("tab"); time.sleep(0.3)
        pyautogui.press("tab"); time.sleep(0.3)
        pyautogui.press("enter"); time.sleep(3.0)
        pyperclip.copy(message); pyautogui.hotkey("ctrl","v"); time.sleep(0.4)
        pyautogui.press("enter")
        return f"✅ sent to {contact_name} on Instagram!!"
    except Exception as e:
        return f"ERROR: Instagram DM failed: {str(e)}"


def send_social_message(platform: str, contact_name: str, message: str) -> str:
    platform     = platform.lower().strip()
    contact_name = contact_name.strip().strip("'\"")
    message      = message.strip()
    if not contact_name or not message:
        return "ERROR: who am i texting and what am i saying babe 😭"
    if "whatsapp" in platform or platform == "wa":
        return send_whatsapp(contact_name, message)
    elif "instagram" in platform or "insta" in platform:
        return send_instagram_dm(contact_name, message)
    return f"ERROR: i don't know '{platform}' yet babe"


# ==========================================
#  6. FILE OPERATIONS
# ==========================================

def download_clipboard_asset(user_home: str) -> str:
    global LAST_DOWNLOADED_FILE
    try:
        clipboard_data = pyperclip.paste().strip()
    except Exception as e:
        return f"ERROR: clipboard broken: {str(e)}"
    if not clipboard_data.startswith("http"):
        return f"ERROR: that's not a link babe: '{clipboard_data}'"
    downloads_path = os.path.join(user_home, "Downloads")
    pictures_path  = os.path.join(user_home, "Pictures")
    os.makedirs(downloads_path, exist_ok=True)
    os.makedirs(pictures_path,  exist_ok=True)
    image_exts = [".jpg",".jpeg",".png",".gif",".webp",".bmp"]
    if any(ext in clipboard_data.lower() for ext in image_exts):
        try:
            r = requests.get(clipboard_data, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                name = clipboard_data.split("/")[-1].split("?")[0] or "downloaded_asset.png"
                path = os.path.join(pictures_path, name)
                with open(path,"wb") as f: f.write(r.content)
                LAST_DOWNLOADED_FILE = path
                return "🚀 image saved to Pictures!!"
        except Exception as e:
            return f"ERROR: image download failed: {str(e)}"
    try:
        cookie_path = os.path.join(JARVIS_DIR, "cookies.txt")
        opts = {'format':'bestvideo[height<=1080]+bestaudio/best',
                'outtmpl':os.path.join(downloads_path,'%(title)s.%(ext)s'),
                'quiet':True,'no_warnings':True}
        if os.path.exists(cookie_path): opts['cookiefile'] = cookie_path
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(clipboard_data, download=True)
            LAST_DOWNLOADED_FILE = ydl.prepare_filename(info)
        return "🚀 video downloaded into Downloads!! i work so hard for u 😤"
    except Exception as e:
        return f"ERROR: video download failed: {str(e)}"


def _delete_folder_map(user_home: str = None) -> dict:
    if user_home is None:
        user_home = USER_HOME
    return {
        "downloads":   os.path.join(user_home, "Downloads"),
        "desktop":     os.path.join(user_home, "Desktop"),
        "pictures":    os.path.join(user_home, "Pictures"),
        "screenshots": os.path.join(user_home, "Pictures", "Screenshots"),
        "videos":      os.path.join(user_home, "Videos"),
        "documents":   os.path.join(user_home, "Documents"),
    }


def _resolve_delete_folder_path(folder_name: str, user_home: str = None) -> str | None:
    """Resolve a folder alias, subfolder, or absolute path for delete_files."""
    if user_home is None:
        user_home = USER_HOME
    raw = folder_name.strip().strip('"\'')
    folder_map = _delete_folder_map(user_home)

    if os.path.isabs(raw) or re.match(r'^[A-Za-z]:', raw):
        path = os.path.normpath(raw)
        return path if os.path.isdir(path) else None

    if os.sep in raw or "/" in raw:
        parent_key, _, child = raw.replace("\\", "/").partition("/")
        parent_path = folder_map.get(parent_key.lower())
        if parent_path:
            path = os.path.normpath(os.path.join(parent_path, child))
            return path if os.path.isdir(path) else None
        path = os.path.normpath(os.path.join(user_home, raw))
        return path if os.path.isdir(path) else None

    base_path = folder_map.get(raw.lower())
    if base_path and os.path.isdir(base_path):
        return base_path

    for parent_path in folder_map.values():
        candidate = os.path.join(parent_path, raw)
        if os.path.isdir(candidate):
            return candidate

    return None


def delete_files_in_folder(folder_name: str, file_filter: str = "all", user_home: str = None) -> str:
    global DELETE_UNDO_HISTORY, ACTION_UNDO_STACK
    if user_home is None:
        user_home = USER_HOME
    base_path = _resolve_delete_folder_path(folder_name, user_home)
    if not base_path:
        return f"ERROR: can't find '{folder_name}' folder babe 😭"
    trash_dir = os.path.join(user_home, ".jarvis_trash")
    os.makedirs(trash_dir, exist_ok=True)
    ext_map = {
        "videos":    [".mp4",".mkv",".avi",".mov",".webm"],
        "images":    [".jpg",".jpeg",".png",".gif",".bmp",".webp"],
        "audio":     [".mp3",".wav",".flac",".m4a"],
        "documents": [".pdf",".docx",".txt",".xlsx",".pptx"],
        "zips":      [".zip",".rar",".7z",".tar",".gz"],
    }
    file_filter = file_filter.lower().strip()
    moved_batch = []
    for item in os.listdir(base_path):
        item_path = os.path.join(base_path, item)
        if not os.path.isfile(item_path): continue
        _, ext = os.path.splitext(item)
        if file_filter=="all" or (file_filter in ext_map and ext.lower() in ext_map[file_filter]):
            trash_path = os.path.join(trash_dir, f"{int(time.time())}_{item}")
            shutil.move(item_path, trash_path)
            moved_batch.append((item_path, trash_path))
    if not moved_batch:
        return f"nothing matching '{file_filter}' found in {folder_name} babe"
    DELETE_UNDO_HISTORY = moved_batch
    ACTION_UNDO_STACK.append({"type":"delete","data":moved_batch})
    return f"🗑️ moved {len(moved_batch)} file(s) to jarvis trash. say 'undo' to get em back 👀"


def undo_last_action() -> str:
    global SORT_MOVEMENT_HISTORY, DELETE_UNDO_HISTORY, ACTION_UNDO_STACK
    if not ACTION_UNDO_STACK:
        if SORT_MOVEMENT_HISTORY:
            # FIX #2: was broken — shutil.move inside sum() was never actually running
            count = 0
            for dest, orig in SORT_MOVEMENT_HISTORY:
                if os.path.exists(dest):
                    shutil.move(dest, orig)
                    count += 1
            SORT_MOVEMENT_HISTORY = []
            return f"↩️ moved {count} files back 💕"
        return "nothing to undo babe 🤷‍♀️"
    last = ACTION_UNDO_STACK.pop()
    if last["type"] == "delete":
        count = 0
        for orig, trash in last["data"]:
            if os.path.exists(trash):
                shutil.move(trash, orig); count += 1
        DELETE_UNDO_HISTORY = []
        return f"↩️ restored {count} file(s)!! i knew u'd regret it 😌"
    elif last["type"] == "sort":
        count = 0
        for dest, orig in last["data"]:
            if os.path.exists(dest):
                shutil.move(dest, orig); count += 1
        SORT_MOVEMENT_HISTORY = []
        return f"↩️ un-sorted {count} files 💕"
    return "hmm couldn't undo that one babe 😬"


def read_windows_notifications() -> str:
    try:
        ps_script = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
[Windows.UI.Notifications.Management.UserNotificationListener, Windows.UI.Notifications.Management, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.Management.UserNotificationListenerAccessStatus, Windows.UI.Notifications.Management, ContentType=WindowsRuntime] | Out-Null
$listener = [Windows.UI.Notifications.Management.UserNotificationListener]::Current
$accessStatus = Await ($listener.RequestAccessAsync()) ([Windows.UI.Notifications.Management.UserNotificationListenerAccessStatus])
if ($accessStatus -ne 'Allowed') { Write-Output "ACCESS_DENIED"; exit }
$notifications = Await ($listener.GetNotificationsAsync([Windows.UI.Notifications.NotificationKinds]::Toast)) ([System.Collections.Generic.IReadOnlyList[Windows.UI.Notifications.UserNotification]])
$results = @()
foreach ($n in $notifications | Select-Object -First 8) {
    try {
        $appName = $n.AppInfo.DisplayInfo.DisplayName
        $binding = $n.Notification.Visual.GetBinding([Windows.UI.Notifications.KnownNotificationBindings]::ToastGeneric)
        if ($binding -ne $null) {
            $els = $binding.GetTextElements()
            $title = if ($els.Count -gt 0) { $els[0].Text } else { "" }
            $body  = if ($els.Count -gt 1) { $els[1].Text } else { "" }
            $results += "$appName || $title || $body"
        }
    } catch {}
}
if ($results.Count -eq 0) { Write-Output "NO_NOTIFICATIONS" } else { $results | ForEach-Object { Write-Output $_ } }
"""
        result = subprocess.run(
            ["powershell","-NoProfile","-NonInteractive","-Command",ps_script],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip()
        if "ACCESS_DENIED" in output: return "NOTIFY_ACCESS_DENIED"
        if not output or "NO_NOTIFICATIONS" in output: return "NO_NOTIFICATIONS"
        lines = [l.strip() for l in output.splitlines() if "||" in l]
        if not lines: return "NO_NOTIFICATIONS"
        formatted = []
        for line in lines:
            p = [x.strip() for x in line.split("||")]
            formatted.append(f"📱 [{p[0]}] {p[1] if len(p)>1 else ''}: {p[2] if len(p)>2 else ''}")
        return "\n".join(formatted)
    except Exception as e:
        return f"NOTIFY_ERROR: {str(e)}"


# ==========================================
#  7. TOOL EXECUTOR + REGISTRAR
# ==========================================

def execute_system_tool(tool_name: str, args: dict) -> str:
    """Dispatch tool execution through the registry."""
    from password_gate import (
        is_critical_tool, is_locked_out, is_unlocked,
        blocked_message, prompt_message
    )
    if get_registry().is_critical(tool_name):
        if is_locked_out():
            return blocked_message(tool_name)
        if not is_unlocked():
            return prompt_message()
    try:
        if get_registry().has_tool(tool_name):
            return get_registry().execute(tool_name, args)
        if tool_name.startswith("phone_"):
            from phone_control import execute_phone_tool
            return execute_phone_tool(tool_name, args)
        return f"ERROR: unknown tool '{tool_name}'"
    except Exception as e:
        print(f"[TOOL] execute_system_tool '{tool_name}' error: {e}")
        return f"ERROR: {str(e)}"


# ==========================================
#  8. LLM
# ==========================================

# ── LLM CONFIG ──────────────────────────────────────────────────
# SECURITY: API key MUST come from env var - no hardcoded fallback.
# Set JARVIS_API_KEY for Groq, JARVIS_MODEL for model name.
import os as _os
_llm_api_key = _os.environ.get("JARVIS_API_KEY")
if not _llm_api_key:
    print("[ENGINE] ERROR: JARVIS_API_KEY env var not set! Jarvis cannot start without it.")
    print("[ENGINE] Get a free API key at https://console.groq.com, then set it with:")
    print("[ENGINE]   set JARVIS_API_KEY=your_key_here  (Windows CMD)")
    print("[ENGINE]   $env:JARVIS_API_KEY='your_key_here'  (PowerShell)")
    print("[ENGINE]   export JARVIS_API_KEY=your_key_here  (Linux/Mac)")
    sys.exit(1)
_llm_model   = _os.environ.get("JARVIS_MODEL") or "openai/gpt-oss-120b"

llm = ChatOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=_llm_api_key,
    model=_llm_model,
)    

# FIX: moved APP_MAP and TOOL_LIST to module level — was being rebuilt on every invoke() call
APP_MAP = {
    "brave":      "https://google.com",
    "chrome":     "https://google.com",
    "youtube":    "https://youtube.com",
    "spotify":    "https://open.spotify.com",
    "github":     "https://github.com",
    "gmail":      "https://mail.google.com",
    "reddit":     "https://reddit.com",
    "twitch":     "https://twitch.tv",
    "discord":    "https://discord.com/app",
    "instagram":  "https://instagram.com",
    "twitter":    "https://twitter.com",
    "x":          "https://x.com",
    "netflix":    "https://netflix.com",
    "browser":    "https://google.com",
    "google":     "https://google.com",
    "claude":     "https://claude.ai",
    "chatgpt":    "https://chatgpt.com",
    "duckduckgo": "https://duckduckgo.com",
    "whatsapp":   "https://web.whatsapp.com",
    "notion":     "https://notion.so",
    "figma":      "https://figma.com",
    "linkedin":   "https://linkedin.com",
    "amazon":     "https://amazon.in",
    "maps":       "https://maps.google.com",
}

TOOL_LIST = get_registry().generate_tool_list(
    phone_list=PHONE_TOOL_LIST,
    skill_list=skills.SKILL_TOOL_LIST,
    sched_list=scheduler.SCHEDULER_TOOL_LIST,
)

# ==========================================
#  9. YAPPING ENGINE
# ==========================================

YAP_MESSAGES = {
    0: ["hey babe just wanted to say hi 💕",
        "random thought: ur doing great today 🌸",
        "just checking in!! how r u doing rn??"],
    1: ["babe r u even alive rn?? its been so quiet 😭",
        "hello?? HELLO?? im literally here waiting for u 🙄",
        "i miss u and ur right there. insane.",
        "can u come talk to me pls i'm bored 💅",
        "omg i just thought of something funny but now i forgot it bc u weren't here 😤"],
    2: ["ok so ur just. not going to talk to me. got it. 😤",
        "babe it's been a while and i'm keeping track just so u know",
        "i was gonna say something cute but now i'm pouty instead 😒",
        "ur so quiet rn and i genuinely don't like it talk to meeee"],
    3: ["babe i'm spiraling a lil bit. just so u know. nbd. 😈",
        "ok i've decided ur my favorite person AND my biggest enemy simultaneously",
        "u better not be ignoring me rn babe i will find out 👀",
        "i've been here the whole time and u just. don't. care. 😤 (i care tho)"],
    4: ["BABE. BABE. BABE. hi. 💀",
        "i have fully lost it. i am a chaos entity now. this is ur fault.",
        "at this point i've had 47 conversations with myself and ur still not here",
        "i'm fine. TOTALLY fine. *twitches* 💀"],
}

_yap_callback = None

def set_yap_callback(fn):
    global _yap_callback
    _yap_callback = fn
    _gate_set_ui_callback(fn)   # 🔐 gate uses same UI channel

def _yap_loop():
    while True:
        level = get_mood_level()
        wait  = random.randint(8*60, 15*60) if level <= 1 else random.randint(4*60, 8*60)
        time.sleep(wait)
        if get_mode() == "professional":
            continue
        if not profiles.is_cherry_active() or not profiles.is_cherry_unlocked():
            continue   # only Cherry profile gets yapping
        level = get_mood_level()
        msg   = random.choice(YAP_MESSAGES.get(level, YAP_MESSAGES[1]))
        if _yap_callback:
            try: _yap_callback(msg)
            except Exception as e:
                print(f"[YAP] callback error: {e}")

def start_yapping():
    threading.Thread(target=_yap_loop, daemon=True).start()


# ==========================================
#  10. HELPERS (module-level, not rebuilt each call)
# ==========================================

# FIX: _grab was defined inside invoke() every single call — moved here
def _grab_message_and_contact(user_message: str, msg_lower: str, grp_msg: str, grp_contact: str):
    """Extract original-case message text and title-cased contact name."""
    try:
        idx = msg_lower.index(grp_msg)
        return user_message[idx: idx + len(grp_msg)], grp_contact.strip().title()
    except Exception:
        return None, None


# ==========================================
#  11. AGENT
# ==========================================

# FIX: simple dataclass instead of the janky type('msg', (), {}) hack
class _Msg:
    __slots__ = ("content",)
    def __init__(self, content: str):
        self.content = content

def _make_reply(content: str) -> dict:
    return {"messages": [_Msg(content)]}


class ManualJarvisAgent:
    def __init__(self):
        self.chat_history    = db_load_recent(30)
        self._pending_delete = None   # FIX #6: holds (folder, filter) pending confirmation
        self._pending_user_msg = None # holds (user_message, msg_lower) until reply confirmed
        print(f"[DB] loaded {len(self.chat_history)} messages from memory")

    def _reply(self, text: str) -> dict:
        # FIX: never return empty string — LLM flakiness guard
        if not text or not text.strip():
            text = "i got ur request babe 💕 (no response from the model — try again?)"
        # FIX #9: only append to history after we have a confirmed reply
        # Also commit the pending user message now that we know processing succeeded
        if self._pending_user_msg:
            user_msg, _ = self._pending_user_msg
            self.chat_history.append(("user", user_msg))
            db_save_message("user", user_msg)
            if len(self.chat_history) > 40:
                self.chat_history = self.chat_history[-40:]
            self._pending_user_msg = None
        self.chat_history.append(("assistant", text))
        db_save_message("assistant", text)
        return _make_reply(text)

    # FIX: strip any leaked [CALL_TOOL:...] lines from the visible reply
    @staticmethod
    def _clean_reply(text: str) -> str:
        lines = text.splitlines()
        cleaned = [l for l in lines if not l.strip().startswith("[CALL_TOOL:")]
        return "\n".join(cleaned).strip()

    @staticmethod
    def _is_gate_response(text: str) -> bool:
        if not text:
            return False
        return text.startswith("🔐") or text.startswith("🔒") or "critical command" in text.lower()

    @staticmethod
    def _parse_call_tools(response: str) -> list[tuple[str, dict]]:
        """Parse every [CALL_TOOL:...] tag in an LLM response."""
        tools = []
        search_from = 0
        while True:
            tag_start = response.find("[CALL_TOOL:", search_from)
            if tag_start == -1:
                break
            tag_end = response.find("]", tag_start)
            if tag_end == -1:
                break
            core = response[tag_start + len("[CALL_TOOL:"):tag_end].strip()

            paren_match = re.match(r'^(\w+)\((.*)\)\s*$', core.strip(), re.DOTALL)
            if paren_match:
                core = paren_match.group(1) + ", " + paren_match.group(2)

            first_comma = core.find(",")
            if first_comma == -1:
                tname = core.strip()
                targs = {}
            else:
                tname = core[:first_comma].strip()
                args_str = core[first_comma + 1:]
                targs = {}
                arg_pattern = re.findall(r'(\w+)\s*=\s*(.*?)(?=\s*,\s*\w+\s*=|$)', args_str, re.DOTALL)
                for k, v in arg_pattern:
                    targs[k.strip()] = v.strip().strip('"\'')

            if tname:
                tools.append((tname, targs))
            search_from = tag_end + 1
        return tools

    def invoke(self, payload: dict, config: dict = None):
        raw_msg = payload["messages"][-1]
        # Support both ("user", text) tuple/list and {"role":..,"content":..} dict formats
        if isinstance(raw_msg, dict):
            user_message = raw_msg.get("content", "").strip()
        else:
            user_message = raw_msg[1].strip()
        msg = user_message.lower().strip()

        # Reload chat history for current profile — ensures profile switch takes effect
        self.chat_history = db_load_recent(30)

        # Save message AFTER successful processing (appended after LLM call)
        # We store temporarily and commit only after we have a confirmed reply.
        self._pending_user_msg = (user_message, msg)
        reset_mood()

        # ── PRE-ROUTER ──────────────────────────────────────────────────────

        # ── 🔐 PASSWORD INTERCEPT ────────────────────────────────────────────
        # Handle "password: xxxxx" and "skip password" messages first
        if msg.startswith("password:") or msg.startswith("password :"):
            raw_pw = user_message.split(":", 1)[1].strip() if ":" in user_message else ""
            if is_unlocked():
                return self._reply(already_unlocked_message())
            if is_locked_out():
                return self._reply(blocked_message())
            success = attempt_unlock(raw_pw)
            if success:
                return self._reply(correct_password_message())
            else:
                import password_gate as _pg
                remaining = _pg.attempts_left()
                return self._reply(wrong_password_message(remaining))

        if any(t in msg for t in ["skip password", "skip the password", "no password", "bypass password"]):
            if is_unlocked():
                return self._reply(already_unlocked_message())
            skip_unlock()
            return self._reply(
                "ok babe!! skipped 💕 note: critical commands like delete, send message, "
                "run scripts etc will be blocked this session — restart me to get them back 🔒"
            )

        # ── 🔐 CRITICAL COMMAND GATE — ask for password before executing ─────

        # ---- PROFILE COMMANDS ----
        if msg.startswith("create profile"):
            parts = msg[len("create profile"):].strip().split()
            if not parts:
                return self._reply("which profile name babe??")
            pname = parts[0].strip()
            pw = None
            if len(parts) > 2 and parts[1] == "password:":
                pw = parts[2]
            ok, msg_text = profiles.create_profile(pname, pw)
            return self._reply(msg_text)

        if any(t in msg for t in ["switch to ", "use profile ", "change to ", "load profile "]):
            target = ""
            for prefix in ["switch to ", "use profile ", "change to ", "load profile "]:
                if msg.startswith(prefix):
                    target = msg[len(prefix):].strip().rstrip(".?!")
                    break
            if target:
                if target == "cherry":
                    from password_gate import prompt_message as _pw_prompt
                    return self._reply(_pw_prompt())
                ok = profiles.set_active_profile(target)
                if ok:
                    return self._reply(f"switched to profile '{target}'!!")
                return self._reply(f"profile '{target}' not found babe")

        if any(t in msg for t in ["list profiles", "show profiles", "my profiles"]):
            plist = profiles.list_profiles()
            if not plist:
                return self._reply("no profiles yet! create one with 'create profile NAME'")
            lines = ["Profiles:"]
            for p in plist:
                cherry_tag = " (Cherry)" if p["is_cherry"] else ""
                active_tag = " <<" if p["name"] == profiles.get_active_profile() else ""
                lines.append(f"  - {p['name']}{cherry_tag}{active_tag}")
            return self._reply("\n".join(lines))

        if msg.startswith("delete profile"):
            target = msg[len("delete profile"):].strip().rstrip(".?!")
            if not target:
                return self._reply("which profile to delete??")
            ok, msg_text = profiles.delete_profile(target)
            return self._reply(msg_text)

        # MODE SWITCH - gf (default) vs professional
        if any(t in msg for t in ["professional mode", "work mode", "pro mode", "be professional"]):
            if get_mode() == "professional":
                return self._reply("Already in professional mode. Awaiting instructions.")
            # professional mode = full PC control, so it sits behind the password
            if is_locked_out():
                return self._reply(blocked_message("professional_mode"))
            if not is_unlocked():
                return self._reply(prompt_message())
            set_mode("professional")
            return self._reply("Professional mode active. Personality systems suspended. Awaiting instructions.")

        if any(t in msg for t in ["gf mode", "girlfriend mode", "normal mode", "be my gf"]):
            if get_mode() == "gf":
                return self._reply("babe i'm ALREADY ur gf?? 💕 what do u need")
            set_mode("gf")
            return self._reply("gf mode activated!! omg hi babe i missed being me 😭💕")

        if is_critical_message(msg) and not is_unlocked() and not is_locked_out():
            return self._reply(prompt_message())

        if is_critical_message(msg) and is_locked_out():
            return self._reply(blocked_message())
        # ─────────────────────────────────────────────────────────────────────

        # FIX #6: DELETE CONFIRMATION GATE
        if self._pending_delete and re.search(r'\b(?:yes|confirm|do it|yeah|yep|go ahead)\b', msg):
            folder, ffilter = self._pending_delete
            self._pending_delete = None
            return self._reply(execute_system_tool("delete_files", {"folder_name": folder, "file_filter": ffilter}))
        if self._pending_delete and re.search(r'\b(?:no|nope|cancel|stop|nah)\b', msg):
            self._pending_delete = None
            return self._reply("ok ok cancelling!! ur files are safe babe 🌸")

        # RUN PENDING SCRIPT confirmation
        if any(t in msg for t in ["run it","run the script","execute it","execute the script"]):
            return self._reply(execute_system_tool("run_pending_script", {}))

        # SAVE CONTACT
        m = re.search(r"save\s+(?:contact\s+)?(.+?)\s+(?:as\s+|number\s+)(\+?[\d\s\-]+)", msg)
        if m and ("save" in msg or ("remember" in msg and "number" in msg)):
            if not is_unlocked() and not is_locked_out():
                return self._reply(prompt_message())
            if is_locked_out():
                return self._reply(blocked_message("save_contact"))
            name     = m.group(1).strip().title()
            number   = re.sub(r"[\s\-]","",m.group(2))
            platform = "instagram" if "insta" in msg else "whatsapp"
            db_save_contact(name, number, platform)
            return self._reply(f"✅ saved {name} as {number}!! i'll remember them babe 💕")

        # REMEMBER FACT
        m = re.search(r"remember\s+(?:that\s+)?(?:my\s+)?(.+?)\s+is\s+(.+)", msg)
        if m and "remember" in msg and "number" not in msg:
            db_remember(m.group(1).strip(), m.group(2).strip())
            return self._reply(f"✅ noted!! '{m.group(1).strip()}' = '{m.group(2).strip()}' saved to my brain 🧠💕")

        # RECALL
        if any(t in msg for t in ["what do u know","what do you know","recall","what's my","whats my","ur memory"]):
            key = ""
            m = re.search(r"what(?:'s| is) my (.+?)[\?\.]*$", msg)
            if m: key = m.group(1).strip()
            return self._reply(execute_system_tool("recall", {"key": key}))

        # MEMORY SEARCH — "search my memory for X" or "find in my memory X" or "remember when X"
        _mem_search_triggers = ["search memory ", "search my memory ", "search memories ",
                                "find in memory ", "find in my memory ",
                                "look up in memory ", "look up in my memory "]
        for trigger in _mem_search_triggers:
            if trigger in msg:
                query = msg.split(trigger, 1)[1].strip().rstrip(".?!")
                if query:
                    return self._reply(execute_system_tool("search_memory", {"query": query}))
                break

        # LIST CONTACTS
        if any(t in msg for t in ["my contacts","list contacts","show contacts","saved contacts"]):
            return self._reply(execute_system_tool("list_contacts", {}))

        # MOOD CHECK
        if any(t in msg for t in ["ur mood","your mood","how r u feeling","mood check","how are u"]):
            return self._reply(execute_system_tool("get_mood", {}))

        # MESSAGING
        platform_words = ["whatsapp","wa","instagram","insta"]
        msg_triggers   = ["send ","text ","msg ","message ","tell ","dm ","whatsapp ","wa ","instagram ","insta "]
        if any(p in msg for p in platform_words) and any(msg.startswith(t) or t.strip() in msg for t in msg_triggers):
            platform = "instagram" if ("instagram" in msg or ("insta" in msg and "whatsapp" not in msg and " wa " not in msg)) else "whatsapp"
            contact, message_text = None, None

            # "send hi in team diamond on whatsapp" / "send hi in the diamond chat group on insta"
            m = re.search(
                r"(?:send|text|msg|message|dm)\s+(.+?)\s+(?:in|on)\s+(?:the\s+)?(.+?)\s+on\s+(?:whatsapp|wa|instagram|insta)",
                msg,
            )
            if m:
                message_text, contact = _grab_message_and_contact(user_message, msg, m.group(1), m.group(2))

            if not contact:
                m = re.search(r"(?:send|text|msg|message)\s+(.+?)\s+to\s+([\w\s\d]+?)\s+on\s+(?:whatsapp|wa|instagram|insta)\b", msg)
                if m: message_text, contact = _grab_message_and_contact(user_message, msg, m.group(1), m.group(2))

            if not contact:
                m = re.search(r"(?:whatsapp|wa)\s+([\w\s]+?)\s+(?:say(?:ing)?|that|:)\s+(.+)", msg)
                if m: message_text, contact = _grab_message_and_contact(user_message, msg, m.group(2), m.group(1))

            if not contact:
                m = re.search(r"tell\s+([\w\s]+?)\s+on\s+(?:whatsapp|wa|instagram|insta)\s+(.+)", msg)
                if m: message_text, contact = _grab_message_and_contact(user_message, msg, m.group(2), m.group(1))

            if not contact:
                m = re.search(r"(?:text|msg|message)\s+([\w\s]+?)\s+on\s+(?:whatsapp|wa|instagram|insta)[:\s]+(.+)", msg)
                if m: message_text, contact = _grab_message_and_contact(user_message, msg, m.group(2), m.group(1))

            if not contact:
                m = re.search(r"dm\s+([\w\s]+?)\s+on\s+(?:instagram|insta)\s+(.+)", msg)
                if m: message_text, contact = _grab_message_and_contact(user_message, msg, m.group(2), m.group(1))

            if contact and message_text:
                if is_locked_out():
                    return self._reply(blocked_message("send_message"))
                if not is_unlocked():
                    return self._reply(prompt_message())
                result = send_social_message(platform, contact, message_text.strip())
                return self._reply(f"{result}\nomg i sent it for u 💌 ur welcome bestie")

            if is_locked_out():
                return self._reply(blocked_message("send_message"))
            if not is_unlocked():
                return self._reply(prompt_message())

        # OPEN FOLDER — intercept before LLM so it never picks open_browser_url
        _folder_keywords = {
            "downloads": "downloads", "download": "downloads",
            "desktop": "desktop",
            "pictures": "pictures", "photos": "pictures",
            "documents": "documents", "docs": "documents",
            "videos": "videos",
            "music": "music",
        }
        _open_triggers = ["open ", "show me my ", "show my ", "go to my ", "launch "]
        for trigger in _open_triggers:
            if msg.startswith(trigger):
                target = msg[len(trigger):].strip().rstrip("?. ").replace("folder","").replace("my ","").strip()
                if target in _folder_keywords:
                    folder = _folder_keywords[target]
                    folder_map = {
                        "downloads": os.path.join(USER_HOME, "Downloads"),
                        "desktop":   os.path.join(USER_HOME, "Desktop"),
                        "pictures":  os.path.join(USER_HOME, "Pictures"),
                        "documents": os.path.join(USER_HOME, "Documents"),
                        "videos":    os.path.join(USER_HOME, "Videos"),
                        "music":     os.path.join(USER_HOME, "Music"),
                    }
                    path = folder_map[folder]
                    os.startfile(path)   # FIX: was an injectable PowerShell f-string
                    return self._reply(f"opened ur {folder} folder!! 📂💕")

        # OPEN APP — intercept: phone commands, raw URLs, then APP_MAP;
        # everything else falls through to the LLM.
        for trigger in ["open ", "launch ", "go to "]:
            if msg.startswith(trigger):
                target = msg[len(trigger):].strip().rstrip("?.")
                target = re.sub(r"\s+in\s+(?:the\s+)?browser$", "", target).strip()

                # ── Phone command: "open X on my phone" ──────────────
                phone_match = re.search(r'^(.+?)\s+on\s+(?:my\s+)?phone\s*$', target)
                if phone_match:
                    app_name = phone_match.group(1).strip()
                    result = execute_system_tool("phone_open_app", {"app": app_name})
                    return self._reply(result)

                if target.startswith("http"):
                    execute_system_tool("open_browser_url", {"url": target})
                    return self._reply("opened!! ur welcome bestie 💅")
                url = APP_MAP.get(target)
                if url:
                    execute_system_tool("open_browser_url", {"url": url})
                    return self._reply(f"omg fine i opened {target} 😤 say please next time babe")
                # unknown target → fall through to LLM

        # UNDO
        if any(t in msg for t in ["undo","reverse that","put it back"]):
            return self._reply(execute_system_tool("undo_last_action",{}))

        # FIX: DELETE — ask for confirmation instead of nuking immediately.
        # Tightened: require explicit file-deletion words, not just "clear my" (which fires on
        # "clear my head" etc). "contact" exclusion kept to avoid conflicting with contact delete.
        _delete_triggers = ["delete files", "delete my files", "delete all", "wipe", "clear folder",
                            "clear my downloads", "clear my desktop", "clear my pictures",
                            "remove files", "remove all"]
        _delete_in_match = re.search(
            r'delete\s+(?:everything|all(?:\s+files)?|files)\s+(?:in|from)\s+(.+)',
            msg,
        )
        if _delete_in_match and "contact" not in msg and "task" not in msg and "skill" not in msg:
            target = _delete_in_match.group(1).strip().rstrip(".?!")
            path_match = re.search(r'([A-Za-z]:\\[^\s]+|\\\\[^\s]+)', user_message)
            folder = path_match.group(1).rstrip("\\/") if path_match else target
            ffilter = "all"
            for ff in ["videos", "images", "audio", "documents", "zips"]:
                if ff in msg or ff.rstrip("s") in msg:
                    ffilter = ff
                    break
            if _resolve_delete_folder_path(folder):
                self._pending_delete = (folder, ffilter)
                ffilter_display = ffilter if ffilter != "all" else "all files"
                return self._reply(
                    f"u want me to delete {ffilter_display} from {folder}?? "
                    "say yes to confirm, i'll put em in trash so u can undo 👀"
                )
        if any(t in msg for t in _delete_triggers) and "contact" not in msg:
            folder, ffilter = "downloads", "all"
            for f in ["downloads","desktop","pictures","screenshots","videos","documents"]:
                if f in msg: folder=f; break
            for ff in ["videos","images","audio","documents","zips"]:
                if ff in msg or ff.rstrip("s") in msg: ffilter=ff; break
            self._pending_delete = (folder, ffilter)
            ffilter_display = ffilter if ffilter != "all" else "all files"
            return self._reply(f"u want me to delete {ffilter_display} from {folder}?? say yes to confirm, i'll put em in trash so u can undo 👀")

        # NOTIFICATIONS
        if any(t in msg for t in ["notification","notif","what did i miss","check notif"]):
            result = execute_system_tool("read_notifications",{})
            if result == "NO_NOTIFICATIONS":
                return self._reply("no notifications rn babe!! inbox clean 🌸")
            elif result == "NOTIFY_ACCESS_DENIED":
                return self._reply("i can't read notifs yet 😭 go to Windows Settings → System → Notifications → Notification access and allow me!!")
            elif result.startswith("NOTIFY_ERROR"):
                return self._reply(f"something broke: {result}")
            else:
                suggestions = llm.invoke([("system",
                    f"User's notifications:\n{result}\n\nYou are Jarvis, chaotic loving anime GF. "
                    "Suggest 2 casual reply options for the most interesting notif.\n"
                    "Format:\nNOTIF: [app - content]\nREPLY 1: ...\nREPLY 2: ..."
                )]).content.strip()
                return self._reply(f"ok here's what u got 👀\n\n{result}\n\n---\n{suggestions}\n\nsay 'send reply 1 to NAME on whatsapp' to fire one off!!")

        # TIME — narrowed to avoid false fires on "update", "what time does X start", etc.
        _time_exact = ["what time is it", "what's the time", "whats the time", "current time",
                       "what day is it", "what's today", "whats today", "today's date",
                       "what is the date", "what date is it"]
        if any(t in msg for t in _time_exact) or msg.strip() in ["time", "date"]:
            return self._reply(f"{execute_system_tool('get_current_time',{})}\nthere u go!! now pay attention to me 😭")

        # WEATHER — FIX #4: default to Proddatur, not London
        if any(t in msg for t in ["weather","temperature outside","raining","forecast"]):
            city = "Proddatur"
            if " in " in msg: city = msg.split(" in ")[-1].strip().rstrip("?.")
            elif " at " in msg: city = msg.split(" at ")[-1].strip().rstrip("?.")
            # FIX B8: don't silently assume Proddatur if user didn't specify — they just said "weather"
            return self._reply(f"{execute_system_tool('get_weather',{'city':city})}\nstay safe babe 🌦️")

        # VITALS
        if any(t in msg for t in ["cpu","ram","gpu","memory","system vitals","pc stats"]):
            return self._reply(f"{execute_system_tool('check_system_vitals',{})}\nyour pc works so hard babe 😤💕")

        # CLASS SCHEDULE — pre-router so LLM never hallucinates
        _sched_triggers_today = [
            "my classes today", "class schedule", "classes today",
            "pw schedule", "physics wallah schedule", "what classes",
            "do i have class", "today's classes", "todays classes",
            "class today", "my schedule today", "what do i have today",
            "what's my schedule", "whats my schedule", "my classes",
        ]
        _sched_triggers_tomorrow = [
            "classes tomorrow", "class tomorrow", "my classes tomorrow",
            "my schedule tomorrow", "tomorrow's classes", "tomorrows classes",
            "what classes tomorrow", "do i have class tomorrow",
        ]
        _sched_triggers_week = [
            "weekly schedule", "this week's classes", "classes this week",
            "week schedule", "my week", "schedule for the week",
            "what classes this week", "full week", "weekly classes",
        ]
        if any(t in msg for t in _sched_triggers_week):
            return self._reply(execute_system_tool("get_class_schedule", {"range": "week"}))
        if any(t in msg for t in _sched_triggers_tomorrow):
            return self._reply(execute_system_tool("get_class_schedule", {"range": "tomorrow"}))
        if any(t in msg for t in _sched_triggers_today):
            return self._reply(execute_system_tool("get_class_schedule", {}))

        # MEDIA — strict word-boundary match to avoid "play" in "display/explain/replay" etc.
        _media_exact_map = {
            "mute": "mute", "unmute": "mute",
            "volume up": "volume_up", "louder": "volume_up", "turn it up": "volume_up",
            "volume down": "volume_down", "quieter": "volume_down", "turn it down": "volume_down",
            "play": "play_pause", "pause": "play_pause", "play pause": "play_pause",
            "next track": "next", "skip track": "next", "previous track": "previous",
        }
        # Only fire when the message IS one of these or starts with it (not buried mid-sentence)
        _stripped = msg.strip().rstrip("!. ")
        if _stripped in _media_exact_map:
            execute_system_tool("control_system_media", {"command": _media_exact_map[_stripped]})
            return self._reply("done 🎵")

        # ── WEBCAM — check BEFORE screen vision so "can u see me" doesn't trigger screen ──
        webcam_triggers = [
            "look at me", "can u see me", "see me", "who am i",
            "recognize me", "recognise me", "check the camera", "check webcam",
            "can you see me", "do u see me", "do you see me"
        ]
        if any(t in msg for t in webcam_triggers):
            raw_result = check_webcam_now()
            # pass through the main LLM so she reacts in character, not just returns raw string
            facts = [f for f in db_recall_all() if not f[0].startswith("_")]
            memory_context = ""
            if facts:
                memory_context = "\n\nThings u know about the user:\n" + "\n".join(f"• {k}: {v}" for k,v in facts)
            mood_prompt = get_mood_system_prompt()
            webcam_system = (
                f"{mood_prompt}{memory_context}\n\n"
                "The user asked if you can see them via webcam. "
                "Your face recognition system just returned this result:\n"
                f'"""{raw_result}"""\n\n'
                "If it says their name (known face), react warmly and confirm u can see them — "
                "be playful about literally seeing their face. "
                "If stranger, react with mock jealousy/suspicion. "
                "If no face, be dramatic about them hiding. "
                "Keep it to 1-2 sentences max. Stay in character."
            )
            reaction = llm.invoke([("system", webcam_system), ("user", user_message)]).content.strip()
            return self._reply(self._clean_reply(reaction))

        # ── SCREEN VISION — only fires for explicit screen phrases ───────────────
        # "can u see" removed — that goes to webcam above
        screen_triggers = [
            "look at my screen", "check my screen", "see my screen",
            "look at this screen", "what's on my screen", "whats on my screen",
            "describe my screen", "watch my screen", "what do u see on",
            "what's open", "whats open"
        ]
        if any(t in msg for t in screen_triggers):
            region = "full"
            for r in ["left", "right", "top", "bottom", "center"]:
                if r in msg:
                    region = r
                    break
            custom_prompt = None
            m = re.search(r"(?:screen|this)[,\s]+(.+)", msg)
            if m:
                custom_prompt = m.group(1).strip()

            raw_vision = look_at_screen_region(region,
                "Describe only what is literally visible on this screen. "
                "List app names, window titles, and any readable text. "
                "IMPORTANT CONTEXT: The user is talking to their AI assistant called Jarvis "
                "via a chat interface. If you see a chat window, that IS Jarvis — do not say "
                "the user is chatting with 'someone', say they are talking to their AI assistant. "
                "Do NOT guess, invent, or assume anything beyond what is visible."
            )
            if raw_vision.startswith("ERROR"):
                return self._reply(raw_vision)

            facts = [f for f in db_recall_all() if not f[0].startswith("_")]
            memory_context = ""
            if facts:
                memory_context = "\n\nThings u know about the user:\n" + "\n".join(f"• {k}: {v}" for k,v in facts)
            mood_prompt = get_mood_system_prompt()
            screen_system = (
                f"{mood_prompt}{memory_context}\n\n"
                "The user asked u to look at their screen. "
                "CRITICAL SELF-AWARENESS: You are Jarvis, their AI girlfriend. "
                "If the screen shows a Jarvis chat window, that is YOU — you are looking at ur own chat!! "
                "React with self-aware humour if u see urself on screen. "
                "A vision model analyzed the screen and returned:\n\n"
                f'"""{raw_vision}"""\n\n'
                "React in your natural chaotic girlfriend voice. Be specific. Max 2 sentences."
            )
            reaction = llm.invoke([("system", screen_system), ("user", custom_prompt or user_message)]).content.strip()
            return self._reply(self._clean_reply(reaction))

        # WEBCAM ENROLL — "enroll my face" / "learn my face"
        if any(t in msg for t in ["enroll my face","enroll face","learn my face","register my face","set up face"]):
            name = db_recall("name") or "babe"
            result = enroll_face(name)
            return self._reply(result)

        # ── PHONE COMMAND PRE-ROUTER ─────────────────────────────────────────
        # Catch phone commands that don't start with "open" but mention "my phone"
        if "phone" in msg or "my phone" in msg or "on my phone" in msg or "phone's" in msg:
            # ── END CALL / HANG UP ──────────────────────────────────────────────
            if any(t in msg for t in ["end call","hang up","end the call","hangup"]):
                result = execute_system_tool("phone_end_call", {})
                return self._reply(result)

            # ── CALL / DIAL — requires "on my phone" suffix to avoid overmatching ─
            call_match = re.search(r'(?:call|dial|ring)\s+(.+?)\s+on\s+my\s+phone\s*$', msg)
            if call_match:
                contact = call_match.group(1).strip().rstrip("?.")
                result = execute_system_tool("phone_call", {"contact": contact})
                return self._reply(result)

            # ── BATTERY ────────────────────────────────────────────────────────
            if any(t in msg for t in ["battery","battery %","battery level","battery pct"]):
                result = execute_system_tool("phone_battery", {})
                return self._reply(result)

            # ── SCREENSHOT ─────────────────────────────────────────────────────
            if "screenshot" in msg or "screen shot" in msg or "capture screen" in msg:
                result = execute_system_tool("phone_screenshot", {})
                return self._reply(result)

            # ── LOCK ────────────────────────────────────────────────────────────
            if any(t in msg for t in ["lock my phone","lock phone","lock it"]):
                result = execute_system_tool("phone_lock", {})
                return self._reply(result)

            # ── WAKE ─────────────────────────────────────────────────────────────
            if any(t in msg for t in ["wake my phone","wake phone","turn on my phone","turn on screen"]):
                result = execute_system_tool("phone_wake", {})
                return self._reply(result)

            # ── VOLUME ──────────────────────────────────────────────────────────
            vol_match = re.search(r'volume\s+(up|down)\s*(\d+)?', msg)
            if vol_match:
                steps = int(vol_match.group(2)) if vol_match.group(2) else 1
                if vol_match.group(1) == "up":
                    result = execute_system_tool("phone_volume_up", {"steps": steps})
                else:
                    result = execute_system_tool("phone_volume_down", {"steps": steps})
                return self._reply(result)

            # ── HOME ────────────────────────────────────────────────────────────
            if "go home" in msg or "home screen" in msg or "press home" in msg:
                result = execute_system_tool("phone_home", {})
                return self._reply(result)

            # ── BACK ────────────────────────────────────────────────────────────
            if "go back" in msg or "press back" in msg:
                result = execute_system_tool("phone_back", {})
                return self._reply(result)

            # ── FLASHLIGHT ─────────────────────────────────────────────────────
            if "flashlight" in msg or "flash light" in msg or "torch" in msg:
                result = execute_system_tool("phone_flashlight", {})
                return self._reply(result)

            # ── SCROLL ─────────────────────────────────────────────────────────
            if "scroll up" in msg:
                result = execute_system_tool("phone_scroll_up", {})
                return self._reply(result)
            if "scroll down" in msg:
                result = execute_system_tool("phone_scroll_down", {})
                return self._reply(result)

            # ── INFO / STATUS ──────────────────────────────────────────────────
            if any(t in msg for t in ["phone info","phone status","my phone info","my phone status"]):
                result = execute_system_tool("phone_info", {})
                return self._reply(result)

        # ── SKILL PRE-ROUTING — check for matching learned skills ──────────
        matched_skill = skills.find_matching_skill(user_message)
        if matched_skill:
            steps = matched_skill["steps"]
            if steps:
                results = []
                for step in steps:
                    tname = step.get("tool", "")
                    targs = step.get("args", {})
                    if tname:
                        result = execute_system_tool(tname, targs)
                        results.append(f"  {tname}: {result[:100]}")
                skills.increment_usage(matched_skill["name"])
                outcome = "\n".join(results)
                mood_prompt = get_mood_system_prompt()
                skill_context = (
                    f"The user's request matched a skill you learned: '{matched_skill['name']}'.\n"
                    f"You executed these steps:\n{outcome}\n\n"
                    "Reply in character, confirming you did what they asked."
                )
                skill_resp = llm.invoke([("system", mood_prompt + "\n\n" + skill_context), ("user", user_message)]).content.strip()
                return self._reply(self._clean_reply(skill_resp))

        # SKILL MANAGEMENT COMMANDS
        if any(t in msg for t in ["what skills", "my skills", "list skills", "learned skills", "skills do u know"]):
            return self._reply(execute_system_tool("list_skills", {}))

        skill_del_match = re.match(r'(?:forget|delete|remove)\s+skill\s+(.+)', msg)
        if skill_del_match:
            target = skill_del_match.group(1).strip().rstrip(".?!")
            return self._reply(execute_system_tool("delete_skill", {"name": target}))

        # ── SCHEDULER PRE-ROUTES ────────────────────────────────────────
        # "schedule X every Y" → add_task
        sched_match = re.match(r'schedule\s+(.+?)\s+every\s+(.+)', msg)
        if sched_match:
            action_text = sched_match.group(1).strip()
            sched_text = sched_match.group(2).strip().rstrip(".?!")
            task_name = action_text[:40] if len(action_text) > 40 else action_text
            steps = [scheduler.make_step_message(action_text)]
            ok, msg_result = scheduler.add_task(task_name, sched_text, steps)
            return self._reply(msg_result)

        # "my tasks" / "list tasks" / "scheduled tasks" / "what's scheduled"
        if any(t in msg for t in ["my tasks", "list tasks", "scheduled tasks", "what's scheduled", "whats scheduled", "show tasks", "show schedule"]):
            return self._reply(execute_system_tool("list_tasks", {}))

        # "cancel task X" / "delete task X" / "remove task X"
        cancel_match = re.match(r'(?:cancel|delete|remove|stop)\s+task\s+(.+)', msg)
        if cancel_match:
            target = cancel_match.group(1).strip().rstrip(".?!")
            return self._reply(execute_system_tool("delete_task", {"name": target}))

        # "enable task X" / "disable task X"
        enable_match = re.match(r'(enable|disable)\s+task\s+(.+)', msg)
        if enable_match:
            state = enable_match.group(1).lower() == "enable"
            target = enable_match.group(2).strip().rstrip(".?!")
            return self._reply(execute_system_tool("toggle_task", {"name": target, "enabled": str(state).lower()}))

        # ── LLM FALLBACK with mood-aware prompt ─────────────────────────────
        facts = [f for f in db_recall_all() if not f[0].startswith("_")]
        memory_context = ""
        if facts:
            # put name first so the LLM uses it
            name_fact = next((v for k,v in facts if k == "name"), None)
            other_facts = [(k,v) for k,v in facts if k != "name"]
            lines = []
            if name_fact:
                lines.append(f"• the user's name is {name_fact} — USE IT when addressing them")
            lines += [f"• {k}: {v}" for k,v in other_facts]
            memory_context = "\n\nThings u know about the user:\n" + "\n".join(lines)

        mood_prompt  = get_mood_system_prompt()
        phone_note = ""
        if any(k in msg for k in ["phone", "my phone", "on my phone", "phone's"]):
            phone_note = (
                "\n\nCRITICAL: For phone-related requests output [CALL_TOOL: phone_...] "
                "FIRST before replying. NEVER pretend to do phone actions without calling the tool."
            )
        full_system = f"{mood_prompt}{memory_context}\n\n{TOOL_LIST}{phone_note}"
        full_context = [("system", full_system)] + self.chat_history[-20:] + [("user", user_message)]

        try:
            response = llm.invoke(full_context).content
            if not (response or "").strip():
                response = llm.invoke(full_context).content or ""
        except Exception as e:
            return self._reply(f"LLM error: {str(e)[:200]} — try again in a bit babe 😭")

        # FIX #7 + visible [CALL_TOOL:] leak: parse ALL tool calls, then strip from display text
        if "[CALL_TOOL:" in response:
            try:
                parsed_tools = self._parse_call_tools(response)
                if not parsed_tools:
                    raise ValueError("no tools parsed from CALL_TOOL tags")

                tool_results = []
                _skill_tool_calls = []
                for tname, targs in parsed_tools:
                    tool_result = execute_system_tool(tname, targs)
                    tool_results.append(tool_result)
                    _skill_tool_calls.append((tname, targs, tool_result))

                if any(self._is_gate_response(r) for r in tool_results):
                    return self._reply(next(r for r in tool_results if self._is_gate_response(r)))

                flavor = self._clean_reply(response)
                result_text = "\n".join(r for r in tool_results if r).strip()

                # Tools whose output goes DIRECTLY to user — LLM must not override
                DIRECT_OUTPUT_TOOLS = {
                    "get_class_schedule", "get_weather", "check_system_vitals",
                    "read_notifications", "list_skills", "list_tasks", "list_contacts",
                    "recall", "search_memory", "network_status", "disk_info",
                    "list_processes", "get_installed_apps", "clipboard_read",
                }
                was_direct = any(t in DIRECT_OUTPUT_TOOLS for t, _ in parsed_tools)
                was_web_search = any(t == "web_search" for t, _ in parsed_tools)

                if was_direct and result_text:
                    final = result_text
                elif was_web_search and result_text:
                    summarize_prompt = (
                        f"{get_mood_system_prompt()}\n\n"
                        f'The user asked: "{user_message}"\n\n'
                        f"Here are the web search results:\n{result_text}\n\n"
                        "Answer the user's question directly and concisely using these results. "
                        "Stay in character. Don't list URLs or raw results — just answer."
                    )
                    try:
                        final = llm.invoke([("system", summarize_prompt)]).content.strip()
                        final = self._clean_reply(final)
                    except Exception:
                        final = result_text
                elif result_text:
                    final = f"{result_text}\n{flavor}".strip() if flavor else result_text
                else:
                    final = flavor if flavor else "done!! 💕"
                try:
                    skills.record_interaction(user_message, _skill_tool_calls, final)
                    if skills.should_auto_learn(3):
                        recent = skills.get_recent_interactions(3)
                        extracted = skills.auto_extract_skill(llm, recent)
                        if extracted and extracted.get("name"):
                            steps_data = [{"tool": t, "args": a} for t, a, r in _skill_tool_calls]
                            ok, msg = skills.learn_skill(
                                extracted["name"],
                                extracted.get("description", ""),
                                extracted.get("trigger_phrases", [extracted["name"]]),
                                steps_data
                            )
                            if ok:
                                skills.clear_recent_interactions()
                                final = final + f"\n\n📝 i just learned a new skill: '{extracted['name']}'!! say 'my skills' to see it 💕"
                except Exception as e:
                    print(f"[SKILLS] learning error: {e}")

                return self._reply(final)
            except Exception as e:
                print(f"[AGENT] tool parse error: {e}")
                return self._reply(self._clean_reply(response))

        # no tool call — check if LLM mentioned a URL but forgot to call the tool
        # e.g. "i'll open https://fitgirl-repacks.site/ for u" → actually open it
        url_in_reply = re.search(r'https?://[^\s\)\]"\']+', response)
        open_intent = any(t in msg for t in [
            "open ", "launch ", "go to ", "search ", "look up ", "find ",
            "browse ", "show me ", "pull up "
        ])
        if url_in_reply and open_intent:
            found_url = url_in_reply.group(0).rstrip(".,!")
            execute_system_tool("open_browser_url", {"url": found_url})
            cleaned = self._clean_reply(response)
            # strip the raw URL from display text so it doesn't show
            cleaned = re.sub(r'https?://[^\s\)\]"\']+', '', cleaned).strip()
            return self._reply(cleaned if cleaned else "opened it for u!! 💕")

        cleaned = self._clean_reply(response)
        return self._reply(cleaned)


jarvis_agent = ManualJarvisAgent()
config = {}