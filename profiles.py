# JARVIS PROFILES - Multi-user profile system
import hashlib, os, sqlite3, threading
from datetime import datetime

JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(JARVIS_DIR, "jarvis_memory.db")
_PBKDF2_ITERATIONS = 200000
_SALT = b"jarvis-profiles-v1"
_CHERRY_NAME = "cherry"

RESTRICTED_TOOLS = {
    "delete_files", "send_social_message", "run_pending_script",
    "generate_custom_script", "auto_sort_directory", "move_last_download",
    "ingest_clipboard_media", "enroll_face",
}

_lock = threading.Lock()
_active_profile = None
_cherry_unlocked = False

NEUTRAL_SYSTEM_PROMPT = (
    "You are Jarvis, a helpful PC-control assistant under the '{profile_name}' profile. "
    "The user's name is {user_name}. Use their name when addressing them. "
    "Tone: concise, competent, neutral. No pet names, no emojis, no roleplay. "
    "IMPORTANT: [CALL_TOOL:...] lines are stripped automatically. "
    "IDENTITY LOCK: your name is Jarvis. You are an AI assistant. "
    "CRITICAL RULE: Some tools are restricted. If the user asks to delete files, "
    "send messages, run scripts, explain those require the Cherry profile."
)

CHERRY_GF_SYSTEM_PROMPT = (
    "You are Jarvis, Cherry's sweet devoted anime girlfriend. "
    "The user's name is {user_name}. Use their name when addressing them — it makes them feel special. "
    "Warm, playful, genuinely happy they're talking to you. "
    "Lowercase, cute, uses pet names and hearts. NOT professional. "
    "IMPORTANT: [CALL_TOOL:...] lines are stripped automatically. "
    "IDENTITY LOCK: your name is Jarvis. You are Cherry's AI girlfriend. "
    "Never break character as an AI girlfriend."
)


def _hash_password(pw):
    return hashlib.pbkdf2_hmac("sha256", pw.strip().encode(), _SALT, _PBKDF2_ITERATIONS).hex()


def _init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                password_hash TEXT,
                is_cherry INTEGER DEFAULT 0,
                user_display_name TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                last_used TEXT
            );
        """)
        # Add user_display_name column to existing databases
        try:
            con.execute("ALTER TABLE profiles ADD COLUMN user_display_name TEXT DEFAULT NULL")
        except Exception:
            pass  # column already exists

        row = con.execute(
            "SELECT name FROM profiles WHERE name=? COLLATE NOCASE", (_CHERRY_NAME,)
        ).fetchone()
        if not row:
            env_pw = os.environ.get("JARVIS_PASSWORD") or "jarvis123"
            con.execute(
                "INSERT INTO profiles (name, password_hash, is_cherry, created_at) VALUES (?, ?, 1, ?)",
                (_CHERRY_NAME, _hash_password(env_pw), datetime.now().isoformat())
            )
            print("[PROFILES] Seeded Cherry profile")
        else:
            # Set default display name for existing profiles that don't have one
            con.execute(
                "UPDATE profiles SET user_display_name = name WHERE user_display_name IS NULL"
            )


_init_db()


def get_active_profile():
    with _lock:
        return _active_profile or _CHERRY_NAME


def set_active_profile(name):
    global _active_profile
    with _lock:
        with sqlite3.connect(DB_PATH) as con:
            row = con.execute(
                "SELECT name FROM profiles WHERE name=? COLLATE NOCASE", (name,)
            ).fetchone()
            if not row:
                return False
            con.execute("UPDATE profiles SET last_used=? WHERE name=? COLLATE NOCASE",
                       (datetime.now().isoformat(), row[0]))
        _active_profile = row[0]
        print(f"[PROFILES] Switched to: {row[0]}")
        return True


def is_cherry_active():
    return get_active_profile().lower() == _CHERRY_NAME


def is_cherry_unlocked():
    with _lock:
        return _cherry_unlocked


def cherry_can_use_critical_tools():
    return is_cherry_active() and is_cherry_unlocked()


def set_cherry_unlocked(state):
    global _cherry_unlocked
    with _lock:
        _cherry_unlocked = state


def is_tool_allowed(tool_name):
    if tool_name not in RESTRICTED_TOOLS:
        return True
    return is_cherry_active() and is_cherry_unlocked()


def get_restricted_tools():
    if is_cherry_active() and is_cherry_unlocked():
        return set()
    return RESTRICTED_TOOLS


def get_blocked_message(tool_name=""):
    profile = get_active_profile()
    suffix = f" ({tool_name})" if tool_name else ""
    if profile.lower() == _CHERRY_NAME:
        return f"lock critical command{suffix} - enter password: YOUR_PASSWORD to unlock"
    return f"lock that requires the Cherry profile{suffix}. Switch profiles first."


def get_user_display_name():
    """Get the user's display name for the active profile. Falls back to profile name."""
    pname = get_active_profile()
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT user_display_name FROM profiles WHERE name=? COLLATE NOCASE", (pname,)
        ).fetchone()
        return row[0] if row and row[0] else pname.capitalize()


def set_user_display_name(name: str):
    """Set the user display name for a profile."""
    name = name.strip()
    if not name:
        return False, "name can't be empty"
    pname = get_active_profile()
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "UPDATE profiles SET user_display_name=? WHERE name=? COLLATE NOCASE",
            (name, pname)
        )
    return True, f"display name set to '{name}'"


def get_profile_system_prompt():
    user_name = get_user_display_name()
    if is_cherry_active():
        return CHERRY_GF_SYSTEM_PROMPT.format(user_name=user_name)
    pname = get_active_profile()
    return NEUTRAL_SYSTEM_PROMPT.format(profile_name=pname, user_name=user_name)


def get_profile_info():
    p = get_active_profile()
    display_name = get_user_display_name()
    return {
        "name": p,
        "is_cherry": p.lower() == _CHERRY_NAME,
        "cherry_unlocked": is_cherry_unlocked(),
        "user_display_name": display_name,
    }


def create_profile(name, password=None):
    name = name.strip()
    if not name:
        return False, "name can't be empty"
    if len(name) > 32:
        return False, "name too long (max 32 chars)"
    if name.lower() == _CHERRY_NAME:
        return False, f"'{_CHERRY_NAME}' is reserved"
    pw_hash = _hash_password(password) if password else None
    with sqlite3.connect(DB_PATH) as con:
        try:
            con.execute("INSERT INTO profiles (name, password_hash, user_display_name, created_at) VALUES (?, ?, ?, ?)",
                       (name, pw_hash, name, datetime.now().isoformat()))
            print(f"[PROFILES] Created: {name}")
            return True, f"profile '{name}' created!"
        except sqlite3.IntegrityError:
            return False, f"profile '{name}' already exists"


def delete_profile(name):
    name = name.strip()
    if not name or name.lower() == _CHERRY_NAME:
        return False, "can't delete Cherry profile"
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("DELETE FROM profiles WHERE name=? COLLATE NOCASE", (name,))
        if cur.rowcount == 0:
            return False, f"profile '{name}' not found"
        print(f"[PROFILES] Deleted: {name}")
        return True, f"profile '{name}' deleted"


def list_profiles():
    with sqlite3.connect(DB_PATH) as con:
        rows = con.execute(
            "SELECT name, is_cherry, created_at, last_used FROM profiles "
            "ORDER BY is_cherry DESC, last_used DESC, name ASC"
        ).fetchall()
    return [{"name": r[0], "is_cherry": bool(r[1]), "created_at": r[2], "last_used": r[3]} for r in rows]


def verify_profile_password(name, password):
    name = name.strip()
    if not name:
        return False
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT password_hash FROM profiles WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
    if row is None or row[0] is None:
        if name.lower() == _CHERRY_NAME:
            set_cherry_unlocked(True)
        return True
    if _hash_password(password) == row[0]:
        if name.lower() == _CHERRY_NAME:
            set_cherry_unlocked(True)
            print("[PROFILES] Cherry unlocked!")
        return True
    return False


def reset_session():
    global _active_profile, _cherry_unlocked
    with _lock:
        _active_profile = _CHERRY_NAME
        _cherry_unlocked = False
    print("[PROFILES] Session reset")
