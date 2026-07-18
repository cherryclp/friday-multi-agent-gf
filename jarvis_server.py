import sys
import os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
os.environ["PYTHONUTF8"] = "1"
from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import asyncio
import queue
from avatar_controller import JarvisAvatar
from jarvis_engine import jarvis_agent, config, set_yap_callback, start_yapping, set_window_reaction_callback, start_window_watcher, set_current_mood, get_current_mood, set_language, get_language, LANGUAGES
from webcam_recognition import set_recognition_callback, start_recognition
from webcam_overlay import launch_overlay
from hotkey_summon import start_hotkey_listener
import profiles
import skills
import scheduler
from password_gate import attempt_unlock, skip_unlock

app = Flask(__name__)
# ── CORS ─────────────────────────────────────────────────────────
# Tighten CORS to only localhost origins
CORS(app, origins=["http://localhost:5000", "http://127.0.0.1:5000", "file://"])

avatar = JarvisAvatar()
vts_loop = asyncio.new_event_loop()

def _start_vts_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=_start_vts_loop, args=(vts_loop,), daemon=True).start()

def run_vts(coro):
    future = asyncio.run_coroutine_threadsafe(coro, vts_loop)
    try:
        return future.result(timeout=15)
    except Exception as e:
        print(f"VTS call error: {e}")

# FIX B16: removed unused _yap_callback_fn — _push_to_ui pushes directly to _yap_queue

def _push_to_ui(message: str):
    # We'll use Server-Sent Events or polling — stored in a queue
    _yap_queue.put(message)
    # FIX: run_vts was called directly here, blocking the callback thread.
    # Use a daemon thread just like the /ask route does.
    try:
        threading.Thread(
            target=run_vts,
            args=(avatar.speak_animation(min(len(message) * 0.05, 4.0)),),
            daemon=True
        ).start()
    except Exception:
        pass

# FIX: plain list was mutated from multiple threads - use a thread-safe queue
_yap_queue = queue.Queue()

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    query = data.get("query", "")
    try:
        responses = jarvis_agent.invoke({"messages": [("user", query)]}, config)
        reply = responses["messages"][-1].content
        threading.Thread(
            target=run_vts,
            args=(avatar.speak_animation(min(len(reply) * 0.05, 8.0)),),
            daemon=True
        ).start()

        # ── PASSWORD GATE FLAG ───────────────────────────────────────────────
        # FIX: check whether the backend is asking for a password right now
        from password_gate import is_unlocked, is_locked_out
        needs_password = (
            not is_unlocked()
            and not is_locked_out()
            and "🔐" in reply   # all prompt_message() strings contain this
        )
        return jsonify({
            "reply": reply,
            "needs_password": needs_password,
            "gate_unlocked": is_unlocked(),
            "gate_locked_out": is_locked_out(),
        })
    except Exception as e:
        return jsonify({"reply": f"[SYSTEM FAULT]: {str(e)}", "needs_password": False})


@app.route("/yap_poll", methods=["GET"])
def yap_poll():
    # Electron polls this to get unprompted messages (yaps)
    try:
        return jsonify({"message": _yap_queue.get_nowait()})
    except queue.Empty:
        return jsonify({"message": None})


@app.route("/unlock", methods=["POST"])
def unlock():
    from password_gate import (
        is_locked_out, attempts_left,
        correct_password_message, wrong_password_message,
    )
    data = request.json or {}
    pw   = data.get("password", "").strip()
    ok   = attempt_unlock(pw)

    if ok:
        # Drain any stale "please enter password" messages from the yap queue
        # so they don't re-trigger the modal after a successful unlock.
        drained = 0
        while True:
            try:
                msg = _yap_queue.get_nowait()
                if not msg.startswith("🔐"):
                    _yap_queue.put(msg)   # put non-gate messages back
                drained += 1
                if drained > 50:
                    break
            except queue.Empty:
                break
        return jsonify({"success": True, "message": correct_password_message()})

    return jsonify({
        "success": False,
        "message": wrong_password_message(attempts_left()),
        "locked_out": is_locked_out(),
        "attempts_left": attempts_left(),
    })

@app.route("/needs_password", methods=["GET"])
def needs_password_status():
    """
    Lightweight endpoint the frontend polls to know whether the password
    modal should be showing. Returns the current gate state without
    triggering any LLM call.
    """
    from password_gate import is_unlocked, is_locked_out, attempts_left
    return jsonify({
        "needs_password": not is_unlocked() and not is_locked_out(),
        "unlocked": is_unlocked(),
        "locked_out": is_locked_out(),
        "attempts_left": attempts_left(),
    })


@app.route("/unlock_skip", methods=["POST"])
def unlock_skip():
    skip_unlock()
    return jsonify({"ok": True})

@app.route("/locked_status", methods=["GET"])
def locked_status():
    # FIX: index.html calls this after a failed password attempt to check if locked out.
    # It was missing entirely, causing a 404 and a JS exception in pwSubmit().
    from password_gate import is_locked_out, attempts_left
    return jsonify({
        "locked_out": is_locked_out(),
        "attempts_left": attempts_left()
    })

@app.route("/gate_status", methods=["GET"])
def gate_status():
    # FIX: index.html polls /gate_status to refresh the lock badge.
    # The endpoint was missing, so every poll 404'd and the badge never updated.
    from password_gate import is_unlocked, is_locked_out, attempts_left
    return jsonify({
        "unlocked": is_unlocked(),
        "locked_out": is_locked_out(),
        "attempts_left": attempts_left()
    })

def start_background_services():
    run_vts(avatar.connect_body())
    set_yap_callback(_push_to_ui)
    start_yapping()
    set_window_reaction_callback(_push_to_ui)
    start_window_watcher()
    set_recognition_callback(_push_to_ui)
    import threading
    threading.Timer(8.0, lambda: start_recognition(cam_index=0, check_interval=30)).start()
    launch_overlay()
    start_hotkey_listener()
    # Start scheduler (Flask/Electron mode)
    scheduler.set_scheduler_callback(_push_to_ui)
    scheduler.start_scheduler()
    print(">> All Jarvis services online 💕")

@app.route("/profiles/create", methods=["POST"])
def profile_create():
    data = request.json or {}
    name = data.get("name", "").strip()
    pw = data.get("password", None)
    ok, msg = profiles.create_profile(name, pw)
    return jsonify({"success": ok, "message": msg, "profiles": profiles.list_profiles()})

@app.route("/profiles/switch", methods=["POST"])
def profile_switch():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "no profile name provided"})
    if name.lower() == "cherry":
        return jsonify({"success": False, "needs_password": True, "message": "Cherry profile requires password"})
    ok = profiles.set_active_profile(name)
    if ok:
        return jsonify({"success": True, "message": f"switched to {name}", "profile": profiles.get_profile_info()})
    return jsonify({"success": False, "message": f"profile {name} not found"})

@app.route("/profiles/unlock_cherry", methods=["POST"])
def profile_unlock_cherry():
    data = request.json or {}
    pw = data.get("password", "").strip()
    ok = profiles.verify_profile_password("cherry", pw)
    if ok:
        from password_gate import attempt_unlock as _pg_unlock
        _pg_unlock(pw)
        profiles.set_active_profile("cherry")
        return jsonify({"success": True, "message": "Cherry profile unlocked!", "profile": profiles.get_profile_info()})
    return jsonify({"success": False, "message": "wrong password"})

@app.route("/profile_status", methods=["GET"])
def profile_status():
    info = profiles.get_profile_info()
    info["mood"] = get_current_mood()
    info["language"] = get_language()
    return jsonify(info)


@app.route("/set_mood", methods=["POST"])
def set_mood():
    """Set the mood (sweet/focus/hype/care) for the active profile."""
    data = request.json or {}
    mood = data.get("mood", "").strip().lower()
    if not mood:
        return jsonify({"success": False, "message": "no mood provided"})
    ok, msg = set_current_mood(mood)
    if ok:
        return jsonify({"success": True, "message": msg, "mood": get_current_mood()})
    return jsonify({"success": False, "message": msg})


@app.route("/set_language", methods=["POST"])
def set_language_route():
    """Set the response language (english, telugu, hindi)."""
    data = request.json or {}
    lang = data.get("language", "").strip().lower()
    if not lang:
        return jsonify({"success": False, "message": "no language provided"})
    ok, msg = set_language(lang)
    return jsonify({"success": ok, "message": msg, "language": get_language()})


@app.route("/get_language", methods=["GET"])
def get_language_route():
    """Get the current response language."""
    return jsonify({"language": get_language(), "languages": list(LANGUAGES.keys())})


@app.route("/profiles/set_display_name", methods=["POST"])
def set_display_name():
    """Set the user display name for the active profile."""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "no name provided"})
    ok, msg = profiles.set_user_display_name(name)
    return jsonify({"success": ok, "message": msg, "profile": profiles.get_profile_info()})


@app.route("/profiles/list", methods=["GET"])
def profile_list():
    return jsonify({"profiles": profiles.list_profiles(), "active": profiles.get_active_profile(), "is_cherry": profiles.is_cherry_active(), "cherry_unlocked": profiles.is_cherry_unlocked()})


# ═══════════════════════════════════════════════════════════════
#  SCHEDULER API
# ═══════════════════════════════════════════════════════════════

@app.route("/tasks/list", methods=["GET"])
def tasks_list():
    """Return all scheduled tasks for the active profile."""
    try:
        all_tasks = scheduler.list_tasks()
        return jsonify({"tasks": all_tasks})
    except Exception as e:
        return jsonify({"tasks": [], "error": str(e)})


@app.route("/tasks/toggle", methods=["POST"])
def tasks_toggle():
    """Toggle or set enabled state for a task."""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "no task name provided"})
    enabled = data.get("enabled", None)
    # Convert to proper bool/None
    if isinstance(enabled, bool):
        pass
    elif isinstance(enabled, str) and enabled.lower() in ("true", "1", "yes"):
        enabled = True
    elif isinstance(enabled, str) and enabled.lower() in ("false", "0", "no"):
        enabled = False
    else:
        enabled = None  # toggle
    ok, msg = scheduler.toggle_task(name, enabled)
    return jsonify({"success": ok, "message": msg})


@app.route("/tasks/delete", methods=["POST"])
def tasks_delete():
    """Delete a scheduled task by name."""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "no task name provided"})
    ok = scheduler.delete_task(name)
    if ok:
        return jsonify({"success": True, "message": f"deleted task '{name}'"})
    return jsonify({"success": False, "message": f"task '{name}' not found"})


@app.route("/tasks/get", methods=["GET"])
def tasks_get():
    """Return full details of a single scheduled task."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"task": None, "error": "no task name provided"})
    task = scheduler.get_task(name)
    if task:
        return jsonify({"task": task})
    return jsonify({"task": None, "error": f"task '{name}' not found"})


# ═══════════════════════════════════════════════════════════════
#  SKILLS API
# ═══════════════════════════════════════════════════════════════

@app.route("/skills/list", methods=["GET"])
def skills_list():
    """Return all learned skills for the active profile."""
    try:
        all_skills = skills.list_skills()
        return jsonify({"skills": all_skills})
    except Exception as e:
        return jsonify({"skills": [], "error": str(e)})


@app.route("/skills/delete", methods=["POST"])
def skills_delete():
    """Delete a skill by name."""
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "message": "no skill name provided"})
    ok = skills.delete_skill(name)
    if ok:
        return jsonify({"success": True, "message": f"forgot '{name}'"})
    return jsonify({"success": False, "message": f"skill '{name}' not found"})


@app.route("/skills/get", methods=["GET"])
def skills_get():
    """Return full details of a single skill."""
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"skill": None, "error": "no skill name provided"})
    skill = skills.get_skill(name)
    if skill:
        return jsonify({"skill": skill})
    return jsonify({"skill": None, "error": f"skill '{name}' not found"})


# ═══════════════════════════════════════════════════════════════
#  PW CLASS SCHEDULE API
# ═══════════════════════════════════════════════════════════════

@app.route("/schedule", methods=["GET"])
def schedule_get():
    try:
        from pw_schedule import fetch_schedule
        date_str = request.args.get("date", "").strip() or None
        result = fetch_schedule(date_str)
        return jsonify(result)
    except ImportError:
        return jsonify({"ok": False, "error": "pw_schedule.py not found", "items": []})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "items": []})


@app.route("/schedule/text", methods=["GET"])
def schedule_text():
    try:
        from pw_schedule import fetch_schedule, format_schedule_for_jarvis
        date_str = request.args.get("date", "").strip() or None
        result = fetch_schedule(date_str)
        text = format_schedule_for_jarvis(result)
        return jsonify({"ok": result["ok"], "text": text, "date": result.get("date", "")})
    except Exception as e:
        return jsonify({"ok": False, "text": f"schedule fetch failed: {str(e)}", "date": ""})


@app.route("/schedule/config", methods=["POST"])
def schedule_config():
    try:
        from pw_schedule import set_pw_token, set_pw_batch_id
        data = request.json or {}
        messages = []
        if data.get("token"):
            messages.append(set_pw_token(data["token"]))
        if data.get("batch_id"):
            messages.append(set_pw_batch_id(data["batch_id"]))
        if not messages:
            return jsonify({"success": False, "message": "provide token and/or batch_id"})
        return jsonify({"success": True, "message": " · ".join(messages)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/schedule/config/status", methods=["GET"])
def schedule_config_status():
    try:
        from pw_schedule import get_pw_token, get_pw_batch_id
        token = get_pw_token()
        batch_id = get_pw_batch_id()
        return jsonify({
            "has_token": bool(token),
            "has_batch_id": bool(batch_id),
            "token_preview": (token[:20] + "...") if token else None,
            "batch_id": batch_id,
        })
    except Exception as e:
        return jsonify({"has_token": False, "has_batch_id": False, "error": str(e)})


@app.route("/pw-raw")
def pw_raw():
    """Dump the first 2 raw API items before any parsing — for debugging."""
    import json, requests
    from pw_schedule import get_pw_token, get_pw_batch_id
    token    = get_pw_token()
    batch_id = get_pw_batch_id()
    if not token or not batch_id:
        return f"<pre>missing token={bool(token)} batch_id={bool(batch_id)}</pre>"
    url = f"https://api.penpencil.co/v2/batches/{batch_id}/todays-schedule"
    headers = {
        "Authorization": token,
        "Content-Type":  "application/json",
        "Client-Id":     "5eb393ee95fab7468a79d189",
        "Client-Type":   "WEB",
        "randomId":      "81ac4abf-2ead-4b88-b57a-4a4ff7c3f1ab",
        "Origin":        "https://www.pw.live",
        "Referer":       "https://www.pw.live/",
        "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        r = requests.get(url, headers=headers, params={"batchId": batch_id}, timeout=12)
        data = r.json()
        raw_items = data.get("data", [])
        # show first 2 items fully expanded
        preview = raw_items[:2]
        return f"<pre>STATUS: {r.status_code}\nTOTAL ITEMS: {len(raw_items)}\n\n{json.dumps(preview, indent=2)}</pre>"
    except Exception as e:
        return f"<pre>ERROR: {e}</pre>"


if __name__ == "__main__":
    # FIX B26: Reset password gate state on fresh start
    from password_gate import reset_session as _reset_gate
    _reset_gate()

    print(">> Starting Jarvis backend server...")
    threading.Thread(target=start_background_services, daemon=True).start()
    # bind to localhost only - never expose the PC-control API on the network
    app.run(host="127.0.0.1", port=5000, debug=False)