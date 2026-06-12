from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import asyncio

from avatar_controller import JarvisAvatar
from jarvis_engine import jarvis_agent, config, set_yap_callback, start_yapping, set_window_reaction_callback, start_window_watcher
from webcam_recognition import set_recognition_callback, start_recognition
from webcam_overlay import launch_overlay
from hotkey_summon import start_hotkey_listener
from password_gate import attempt_unlock, skip_unlock

app = Flask(__name__)
CORS(app)  # allows Electron to talk to Flask

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

# This will be set once Electron connects
_yap_callback_fn = None

def _push_to_ui(message: str):
    # We'll use Server-Sent Events or polling — stored in a queue
    _yap_queue.append(message)
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

_yap_queue = []

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
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": f"[SYSTEM FAULT]: {str(e)}"})

@app.route("/yap_poll", methods=["GET"])
def yap_poll():
    # Electron polls this to get unprompted messages (yaps)
    if _yap_queue:
        msg = _yap_queue.pop(0)
        return jsonify({"message": msg})
    return jsonify({"message": None})

@app.route("/unlock", methods=["POST"])
def unlock():
    data = request.json
    pw   = data.get("password", "").strip()
    ok   = attempt_unlock(pw)
    return jsonify({"success": ok})

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
    print(">> All Jarvis services online 💕")

if __name__ == "__main__":
    print(">> Starting Jarvis backend server...")
    threading.Thread(target=start_background_services, daemon=True).start()
    app.run(port=5000, debug=False)