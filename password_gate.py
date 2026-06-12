# ============================================================
#  JARVIS PASSWORD GATE 🔐
#  Protects critical/destructive commands behind a password.
#  - 2 attempts max per session
#  - Session unlock lasts until jarvis.py is restarted
#  - Skipping or failing locks out critical commands for session
# ============================================================

import hashlib
import threading

# ── CONFIG ───────────────────────────────────────────────────
# Change this to your password. Stored as SHA-256 hash so
# plain text is never in memory at runtime.
# Default password: jarvis123  ← CHANGE THIS BABE 💕
# SECURITY FIX: password now comes from the JARVIS_PASSWORD environment
# variable and is hashed with salted PBKDF2 instead of plain SHA-256.
# Falls back to 'jarvis123' ONLY if the env var is missing - set it!
import os as _os

_PBKDF2_ITERATIONS = 200_000
_SALT = b"jarvis-gate-v1"

def _hash_password(pw: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pw.strip().encode(), _SALT, _PBKDF2_ITERATIONS
    ).hex()

_env_pw = _os.environ.get("JARVIS_PASSWORD")
if _env_pw:
    _PASSWORD_HASH = _hash_password(_env_pw)
else:
    print("[GATE] WARNING: JARVIS_PASSWORD env var not set - using default password 'jarvis123'. CHANGE THIS!")
    _PASSWORD_HASH = _hash_password("jarvis123")

MAX_ATTEMPTS = 2

# ── Critical tools that require a password ───────────────────
# These are destructive / privacy / system-level actions.
CRITICAL_TOOLS = {
    "delete_files",
    "send_social_message",
    "run_pending_script",
    "generate_custom_script",
    "auto_sort_directory",
    "move_last_download",
    "ingest_clipboard_media",
    "enroll_face",
}

# Critical phrases in user chat that trigger the gate
# (for the invoke() pre-router, before LLM even runs)
CRITICAL_PHRASES = [
    "delete", "remove files", "wipe", "clear folder",
    "send message", "text", "dm ", "message ",
    "run the script", "run it", "execute",
    "sort my", "sort the", "organise", "organize",
    "enroll my face", "learn my face", "register my face",
    "move download", "download to",
]

# ── Session state ─────────────────────────────────────────────
_lock             = threading.Lock()
_unlocked         = False   # True once correct password entered this session
_locked_out       = False   # True once 2 wrong attempts or skipped
_attempts_used    = 0

# ── Callback — jarvis_engine sets this so we can push UI messages ─
_ui_callback = None

def set_ui_callback(fn):
    global _ui_callback
    _ui_callback = fn

def _push(msg: str):
    if _ui_callback:
        try: _ui_callback(msg)
        except Exception: pass


# ── Core functions ────────────────────────────────────────────

def is_unlocked() -> bool:
    with _lock:
        return _unlocked

def is_locked_out() -> bool:
    with _lock:
        return _locked_out

def is_critical_tool(tool_name: str) -> bool:
    return tool_name in CRITICAL_TOOLS

def is_critical_message(msg_lower: str) -> bool:
    return any(phrase in msg_lower for phrase in CRITICAL_PHRASES)


def attempt_unlock(password: str) -> bool:
    """
    Try to unlock with the given password.
    Returns True if correct, False otherwise.
    After MAX_ATTEMPTS failures → locked out for session.
    """
    global _unlocked, _locked_out, _attempts_used
    with _lock:
        if _unlocked:
            return True
        if _locked_out:
            return False

        guess_hash = _hash_password(password)
        _attempts_used += 1

        if guess_hash == _PASSWORD_HASH:
            _unlocked = True
            print("[GATE] ✅ password correct — critical commands unlocked")
            return True
        else:
            remaining = MAX_ATTEMPTS - _attempts_used
            print(f"[GATE] ❌ wrong password ({_attempts_used}/{MAX_ATTEMPTS})")
            if remaining <= 0:
                _locked_out = True
                print("[GATE] 🔒 locked out for this session")
            return False


def skip_unlock():
    """User chose to skip — lock out critical commands for session."""
    global _locked_out
    with _lock:
        _locked_out = True
    print("[GATE] ⏭ password skipped — critical commands disabled for session")


def reset_session():
    """Call this on jarvis restart to clear state."""
    global _unlocked, _locked_out, _attempts_used
    with _lock:
        _unlocked      = False
        _locked_out    = False
        _attempts_used = 0


def attempts_left() -> int:
    with _lock:
        return max(0, MAX_ATTEMPTS - _attempts_used)


def change_password(old_password: str, new_password: str) -> tuple[bool, str]:
    """
    Change the password. Requires the old password to be correct.
    Returns (success: bool, message: str).
    """
    global _PASSWORD_HASH, _unlocked, _locked_out, _attempts_used
    old_hash = _hash_password(old_password)
    with _lock:
        if old_hash != _PASSWORD_HASH:
            return False, "❌ old password is wrong babe 😤 try again"
        if not new_password.strip():
            return False, "❌ new password can't be empty babe"
        _PASSWORD_HASH = _hash_password(new_password)
        # FIX: reset session so user must re-authenticate with new password.
        # Without this, the session stays permanently unlocked after a change.
        _unlocked      = False
        _locked_out    = False
        _attempts_used = 0
        print(f"[GATE] 🔑 password changed successfully — session reset")
        return True, "✅ password changed!! enter the new one to unlock critical commands again 💕"


# ── Jarvis-flavoured response strings ────────────────────────

def blocked_message(tool_name: str = "") -> str:
    suffix = f" ({tool_name})" if tool_name else ""
    return (
        f"🔒 nope!! that's a critical command{suffix} and u either skipped the password "
        "or ran out of tries babe 😤 restart me if u want another shot~"
    )

def prompt_message() -> str:
    return (
        "🔐 babe this is a critical command!! i need ur password to do this "
        f"(u have {MAX_ATTEMPTS} tries) — type: **password: YOUR_PASSWORD** "
        "or say **skip password** to run without critical access (some things will be blocked 💕)"
    )

def wrong_password_message(attempts_left: int) -> str:
    if attempts_left <= 0:
        return (
            "❌ nope!! wrong password and that was ur last try 💀 "
            "critical commands are locked for this session babe~ "
            "restart jarvis to try again"
        )
    return (
        f"❌ that's not right babe 😤 u have {attempts_left} tr{'y' if attempts_left==1 else 'ies'} left — "
        "type: **password: YOUR_PASSWORD**"
    )

def correct_password_message() -> str:
    return (
        "✅ CORRECT!! ur verified babe 💕🔓 "
        "critical commands unlocked for this session~ now what did u need??"
    )

def already_unlocked_message() -> str:
    return "🔓 already verified babe!! just tell me what u want~"