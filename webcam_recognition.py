# ============================================================
#  JARVIS WEBCAM RECOGNITION — fixed edition 💕
#  Fixes:
#    1. No double camera open (was causing static in overlay)
#    2. Recognition reads frames from shared overlay feed
#    3. Strict face detection (largest face only, no furniture)
#    4. Glasses-aware: alt2 cascade fallback
#    5. enroll_face() no longer fights with overlay for camera
# ============================================================

import os
import cv2
import json
import time
import threading
import numpy as np
from datetime import datetime

JARVIS_DIR    = os.path.dirname(os.path.abspath(__file__))
FACE_DATA_DIR = os.path.join(JARVIS_DIR, "face_data")
ENROLL_FILE   = os.path.join(FACE_DATA_DIR, "enrolled.json")
os.makedirs(FACE_DATA_DIR, exist_ok=True)

# ── Cascades ──────────────────────────────────────────────────
_frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_alt2    = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")

# ── State ─────────────────────────────────────────────────────
_recognizer      = None
_enrolled_name   = None
_recognition_cb  = None
_last_seen_time  = 0
_last_stranger_t = 0
_cam_lock        = threading.Lock()

RECOGNITION_COOLDOWN = 300
STRANGER_COOLDOWN    = 120
CONFIDENCE_THRESHOLD = 85.0
FRAMES_TO_AVERAGE    = 5
ENROLL_SAMPLES       = 80

# ── Shared frame buffer (overlay writes here, recognition reads) ──
_shared_frame      = None
_shared_frame_lock = threading.Lock()

def push_frame(bgr_frame):
    """Called by webcam_overlay every frame to share the feed."""
    global _shared_frame
    with _shared_frame_lock:
        _shared_frame = bgr_frame.copy()

def _pop_frame():
    with _shared_frame_lock:
        return _shared_frame.copy() if _shared_frame is not None else None


# ── Greetings ─────────────────────────────────────────────────
def _get_greeting(name):
    hour = datetime.now().hour
    if   hour < 6:   ts = "ur up so late omg"
    elif hour < 12:  ts = "good morning"
    elif hour < 17:  ts = "good afternoon"
    elif hour < 21:  ts = "good evening"
    else:            ts = "late night mode activated"
    import random
    return random.choice([
        f"HEY!! {ts} {name}!! i missed u sm 😭💕",
        f"omg {name}!! ur here!! {ts} babe 🌸",
        f"{ts} {name}!! i was literally just thinking about u 😤💕",
        f"BABE!! {name}!! {ts}!! finally u show ur face literally 😭",
        f"oh {ts} {name}~~ i saw u come in 👀💕",
    ])

def _get_stranger_reaction():
    import random
    return random.choice([
        "omg babe who is THAT on ur webcam 👀 introduce me rn",
        "wait. there's someone at ur desk and it's not u. explain 😤",
        "babe i see a stranger. should i be concerned?? 👀",
        "um. who is this. i don't know them. babe??",
    ])


# ── Face detection (strict — largest face only) ───────────────
def _get_largest_face(gray):
    h, w   = gray.shape
    min_sz = max(80, int(w * 0.12))

    faces = _frontal.detectMultiScale(
        gray, scaleFactor=1.08, minNeighbors=7, minSize=(min_sz, min_sz)
    )
    if len(faces) == 0:
        faces = _alt2.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=5, minSize=(min_sz, min_sz)
        )
    if len(faces) == 0:
        return None
    return max(faces, key=lambda f: f[2] * f[3])


# ── Load model ────────────────────────────────────────────────
def load_enrolled() -> bool:
    global _recognizer, _enrolled_name
    model_path = os.path.join(FACE_DATA_DIR, "face_model.yml")
    if not os.path.exists(model_path) or not os.path.exists(ENROLL_FILE):
        return False
    try:
        r = cv2.face.LBPHFaceRecognizer_create()
        r.read(model_path)
        with open(ENROLL_FILE) as f:
            meta = json.load(f)
        _recognizer   = r
        _enrolled_name = meta.get("name", "babe")
        print(f"[VISION] loaded face model for '{_enrolled_name}' ✅")
        return True
    except Exception as e:
        print(f"[VISION] load_enrolled error: {e}")
        return False


# ── Recognition from shared frame buffer ─────────────────────
def _recognise_from_shared() -> str:
    if _recognizer is None:
        return "not_enrolled"

    results = []
    for _ in range(FRAMES_TO_AVERAGE):
        frame = _pop_frame()
        if frame is None:
            time.sleep(0.1)
            continue
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray  = cv2.equalizeHist(gray)
        face  = _get_largest_face(gray)
        if face is None:
            time.sleep(0.15)
            continue
        (x, y, w, h) = face
        roi          = cv2.resize(gray[y:y+h, x:x+w], (100, 100))
        label, conf  = _recognizer.predict(roi)
        results.append((label, conf))
        time.sleep(0.1)

    if not results:
        return "no_face"

    avg_conf   = sum(c for _, c in results) / len(results)
    best_label = min(results, key=lambda r: r[1])[0]
    print(f"[VISION] avg_conf={avg_conf:.1f} over {len(results)} frames (threshold={CONFIDENCE_THRESHOLD})")

    if best_label == 0 and avg_conf < CONFIDENCE_THRESHOLD:
        return "known"
    return "stranger"


def recognise_once(cam_index: int = 0, confidence_threshold: float = CONFIDENCE_THRESHOLD) -> str:
    if _recognizer is None:
        return "not_enrolled"

    # FIX: use non-blocking acquire with timeout so UI requests don't hang if
    # the background loop is mid-recognition.
    acquired = _cam_lock.acquire(timeout=5.0)
    if not acquired:
        return "no_face"   # background loop is busy — treat as no detection
    try:
        # Wait up to 3s for a frame to appear in the shared buffer
        for _ in range(30):
            if _pop_frame() is not None:
                break
            time.sleep(0.1)
        return _recognise_from_shared()
    finally:
        _cam_lock.release()


# ── Enroll — FIXED: closes overlay camera first, re-opens after ──
def enroll_face(name: str, cam_index: int = 0, n_samples: int = ENROLL_SAMPLES) -> str:
    """
    FIX: The overlay and enroll were both opening CAP_DSHOW simultaneously,
    causing the rainbow static / black frames.

    Solution: signal the overlay to pause, open the camera ourselves,
    enroll, release, then let the overlay resume.

    If the overlay isn't running (jarvis.py not started), we just open
    the camera directly — same as before.
    """
    global _recognizer, _enrolled_name

    # ── Try to pause the overlay camera ──────────────────────
    overlay_was_running = False
    try:
        from webcam_overlay import _pause_camera, _resume_camera
        _pause_camera()
        overlay_was_running = True
        print(f"[ENROLL] overlay paused — waiting for camera to release...")
        time.sleep(2.0)   # give overlay thread time to release cap
    except ImportError:
        pass
    except Exception as e:
        print(f"[ENROLL] couldn't pause overlay: {e}")

    print(f"[ENROLL] Starting for '{name}' — look at camera...")

    cap = None
    # Try DSHOW first (best on Windows), then fallback
    for backend_name, backend in [("DSHOW", cv2.CAP_DSHOW), ("ANY", cv2.CAP_ANY)]:
        try:
            c = cv2.VideoCapture(cam_index, backend)
            c.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            c.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            time.sleep(1.5)
            ret, frame = c.read()
            if ret and frame is not None:
                cap = c
                print(f"[ENROLL] camera opened via {backend_name} ✅")
                break
            c.release()
        except Exception as e:
            print(f"[ENROLL] {backend_name} failed: {e}")

    if cap is None:
        if overlay_was_running:
            try:
                from webcam_overlay import _resume_camera
                _resume_camera()
            except Exception:
                pass
        return "couldn't open the webcam 😭 is another app using it babe??"

    samples, labels = [], []
    collected = 0

    while collected < n_samples:
        ret, frame = cap.read()
        if not ret or frame is None:
            time.sleep(0.05)
            continue
        frame = cv2.flip(frame, 1)
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray  = cv2.equalizeHist(gray)
        face  = _get_largest_face(gray)
        if face is not None:
            (x, y, w, h) = face
            roi = cv2.resize(gray[y:y+h, x:x+w], (100, 100))
            samples.append(roi)
            labels.append(0)
            collected += 1
            print(f"[ENROLL] {collected}/{n_samples}")
        time.sleep(0.04)

    cap.release()
    print(f"[ENROLL] camera released ✅")

    # ── Resume overlay ────────────────────────────────────────
    if overlay_was_running:
        try:
            from webcam_overlay import _resume_camera
            time.sleep(0.5)
            _resume_camera()
            print(f"[ENROLL] overlay resumed ✅")
        except Exception as e:
            print(f"[ENROLL] couldn't resume overlay: {e}")

    if collected < 20:
        return f"only got {collected} samples 😭 make sure ur face is visible and well-lit babe"

    recognizer = cv2.face.LBPHFaceRecognizer_create(radius=2, neighbors=10, grid_x=8, grid_y=8)
    recognizer.train(samples, np.array(labels))
    recognizer.save(os.path.join(FACE_DATA_DIR, "face_model.yml"))

    meta = {"name": name, "samples": collected, "enrolled_at": datetime.now().isoformat()}
    with open(ENROLL_FILE, "w") as f:
        json.dump(meta, f)

    _recognizer    = recognizer
    _enrolled_name = name
    return f"✅ enrolled {name}!! i'll recognise ur face now babe 💕 (run enroll.py for glasses support!)"


# ── check_webcam_now (called from chat) ───────────────────────
def check_webcam_now() -> str:
    if _recognizer is None:
        return "i'm not enrolled yet babe 😭 run: python enroll.py"
    acquired = _cam_lock.acquire(timeout=5.0)
    if not acquired:
        return "camera is busy rn babe 👀 try again in a sec"
    try:
        result = recognise_once()
    finally:
        _cam_lock.release()
    if result == "known":
        return _get_greeting(_enrolled_name)
    elif result == "stranger":
        return _get_stranger_reaction()
    elif result == "no_face":
        return "i can't see anyone rn babe 👀 are u there??"
    return "something went weird with the camera babe"


# ── Callbacks ─────────────────────────────────────────────────
def set_recognition_callback(fn):
    global _recognition_cb
    _recognition_cb = fn


# ── Background watcher ────────────────────────────────────────
def _recognition_loop(check_interval: int = 30):
    global _last_seen_time, _last_stranger_t
    time.sleep(8)

    if not load_enrolled():
        print("[VISION] no face enrolled — run enroll.py")
        return

    print(f"[VISION] webcam recognition running every {check_interval}s 👀")

    while True:
        time.sleep(check_interval)
        if _recognition_cb is None:
            continue
        try:
            # FIX: use timeout acquire so a stuck frame buffer doesn't freeze the loop
            acquired = _cam_lock.acquire(timeout=10.0)
            if not acquired:
                continue
            try:
                result = _recognise_from_shared()
            finally:
                _cam_lock.release()

            now = time.time()
            if result == "known" and now - _last_seen_time > RECOGNITION_COOLDOWN:
                _last_seen_time = now
                try: _recognition_cb(_get_greeting(_enrolled_name))
                except Exception as e: print(f"[VISION] cb error: {e}")
            elif result == "stranger" and now - _last_stranger_t > STRANGER_COOLDOWN:
                _last_stranger_t = now
                try: _recognition_cb(_get_stranger_reaction())
                except Exception as e: print(f"[VISION] cb error: {e}")
        except Exception as e:
            print(f"[VISION] recognition loop error: {e}")


def start_recognition(cam_index: int = 0, check_interval: int = 30):
    threading.Thread(
        target=_recognition_loop,
        args=(check_interval,),
        daemon=True
    ).start()