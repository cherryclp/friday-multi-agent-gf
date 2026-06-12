import eel
import asyncio
import threading
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
        return reply
    except Exception as e:
        return f"[SYSTEM FAULT]: {str(e)}"

if __name__ == "__main__":
    print(">> STARTING JARVIS CYBER-DECK INTERFACE...")
    print(">> Connecting to VTube Studio...")
    run_vts(avatar.connect_body())

    set_yap_callback(_push_to_ui)
    start_yapping()
    print(">> Yapping engine online 💬")

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