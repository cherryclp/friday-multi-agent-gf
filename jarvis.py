import eel
import asyncio
import threading
import scheduler
from hotkey_summon import start_hotkey_listener, set_summon_callback
from avatar_controller import JarvisAvatar
from jarvis_engine import jarvis_agent, config, set_yap_callback, start_yapping, set_window_reaction_callback, start_window_watcher
from webcam_recognition import set_recognition_callback, start_recognition
from webcam_overlay import launch_overlay   # 👈 shoulder cam bubble 💕

eel.init("web")

avatar   = JarvisAvatar()
vts_loop = asyncio.new_event_loop()

def _start_vts_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

vts_thread = threading.Thread(target=_start_vts_loop, args=(vts_loop,), daemon=True)
vts_thread.start()

def run_vts(coro):
    future = asyncio.run_coroutine_threadsafe(coro, vts_loop)
    try:
        return future.result(timeout=15)
    except Exception as e:
        print(f"VTS call error: {e}")

def _push_to_ui(message: str):
    """Push any unprompted message (yap or screen reaction) into the chat UI."""
    try:
        eel.receive_jarvis_yap(message)
        threading.Thread(
            target=run_vts,
            args=(avatar.speak_animation(min(len(message) * 0.05, 4.0)),),
            daemon=True
        ).start()
    except Exception as e:
        print(f"UI push error: {e}")

@eel.expose
def ask_jarvis(query):
    try:
        responses = jarvis_agent.invoke({"messages": [("user", query)]}, config)
        reply = responses["messages"][-1].content
        animation_duration = min(len(reply) * 0.05, 8.0)
        threading.Thread(
            target=run_vts,
            args=(avatar.speak_animation(animation_duration),),
            daemon=True
        ).start()
        from password_gate import is_unlocked, is_locked_out
        needs_password = (
            not is_unlocked()
            and not is_locked_out()
            and reply.startswith("🔐")
        )
        # Return a dict — JS side: const data = await eel.ask_jarvis(query)()
        # data.reply for the text, data.needs_password to open modal
        return {
            "reply": reply,
            "needs_password": needs_password,
            "gate_unlocked": is_unlocked(),
            "gate_locked_out": is_locked_out(),
        }
    except Exception as e:
        return {"reply": f"[SYSTEM FAULT]: {str(e)}", "needs_password": False}


@eel.expose
def submit_password(password):
    """Called by the password modal in the eel frontend."""
    from password_gate import (
        attempt_unlock, is_unlocked, is_locked_out,
        attempts_left, correct_password_message, wrong_password_message,
    )
    if is_unlocked():
        from password_gate import already_unlocked_message
        return {"success": True, "message": already_unlocked_message()}
    if is_locked_out():
        from password_gate import blocked_message
        return {"success": False, "locked_out": True, "message": blocked_message()}

    ok = attempt_unlock(password.strip())
    if ok:
        return {"success": True, "message": correct_password_message()}
    return {
        "success": False,
        "locked_out": is_locked_out(),
        "attempts_left": attempts_left(),
        "message": wrong_password_message(attempts_left()),
    }


@eel.expose
def skip_password_gate():
    """Called when user hits Skip in the password modal."""
    from password_gate import skip_unlock, is_unlocked
    if not is_unlocked():
        skip_unlock()
    return {"ok": True}


@eel.expose
def get_gate_status():
    """Lets the frontend poll the gate state at any time."""
    from password_gate import is_unlocked, is_locked_out, attempts_left
    return {
        "unlocked": is_unlocked(),
        "locked_out": is_locked_out(),
        "needs_password": not is_unlocked() and not is_locked_out(),
        "attempts_left": attempts_left(),
    }

if __name__ == "__main__":
    # FIX B26: Reset password gate state on fresh start
    from password_gate import reset_session as _reset_gate
    _reset_gate()

    print(">> STARTING JARVIS CYBER-DECK INTERFACE...")
    print(">> Connecting to VTube Studio...")
    run_vts(avatar.connect_body())

    set_yap_callback(_push_to_ui)
    start_yapping()
    print(">> Yapping engine online 💬")

    scheduler.set_scheduler_callback(_push_to_ui)
    scheduler.start_scheduler()
    print(">> Scheduler online ⏰")

    set_window_reaction_callback(_push_to_ui)
    start_window_watcher()
    print(">> Screen watcher online 👀")

    set_recognition_callback(_push_to_ui)
    # delay so overlay cam is fully open before recognition tries to read frames
    threading.Timer(8.0, lambda: start_recognition(cam_index=0, check_interval=30)).start()
    print(">> Webcam recognition online 📷")

    launch_overlay()   # 👈 floating shoulder cam bubble
    print(">> Webcam overlay online 💕 (right-click bubble to close)")

    set_summon_callback(_push_to_ui)
    start_hotkey_listener()
    print(">> Summon hotkey active: Ctrl+Space 💕")

    print(">> Launching UI...")
    eel.start("index.html", size=(980, 700))
    run_vts(avatar.close_body())