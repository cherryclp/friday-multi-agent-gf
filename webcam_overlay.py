# ============================================================
#  JARVIS WEBCAM OVERLAY
#  Fixed: auto-detects working camera backend, reconnects on
#  bad frames, no static glitch
# ============================================================

import cv2
import json
import os
import threading
import time
import tkinter as tk
from collections import deque
import numpy as np
from PIL import Image, ImageTk, ImageDraw, ImageFilter

# ── CONFIG ───────────────────────────────────────────────────
WINDOW_SIZE  = 220
BORDER_COLOR = "#ff6eb4"
CAM_INDEX    = 0
FPS_TARGET   = 20
CORNER       = "bottom-right"
MARGIN       = 18
LABEL_TEXT   = "👀 watching u babe"

# ── Adaptive threshold mirror (same constants as webcam_recognition) ──
_BASE_THRESHOLD    = 160.0
_BRIGHT_TARGET     = 120
_THRESHOLD_RANGE   = 60
_SHARPNESS_MIN     = 30

JARVIS_DIR  = os.path.dirname(os.path.abspath(__file__))
ENROLL_FILE = os.path.join(JARVIS_DIR, "face_data", "enrolled.json")
FACE_MODEL  = os.path.join(JARVIS_DIR, "face_data", "face_model.yml")

# ── Cascades ─────────────────────────────────────────────────
_frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_alt2    = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")

# ── Face Database mirror (reads what webcam_recognition writes) ──
FACE_DB_PATH = os.path.join(JARVIS_DIR, "face_data", "face_db.json")

def _get_largest_face(gray):
    h, w   = gray.shape
    min_sz = max(100, int(w * 0.15))   # match enrollment (15% of frame width)
    faces  = _frontal.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=8, minSize=(min_sz, min_sz), flags=cv2.CASCADE_SCALE_IMAGE)
    if len(faces) == 0:
        faces = _alt2.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5, minSize=(min_sz, min_sz), flags=cv2.CASCADE_SCALE_IMAGE)
    if len(faces) == 0:
        return None
    return max(faces, key=lambda f: f[2] * f[3])

def _load_label_to_name() -> dict:
    """Load the label → name mapping from the face database."""
    try:
        if os.path.exists(FACE_DB_PATH):
            with open(FACE_DB_PATH) as f:
                db = json.load(f)
            return {v["label"]: k for k, v in db.get("persons", {}).items()}
    except Exception as e:
        print(f"[OVERLAY] _load_label_to_name error: {e}")
    return {}

_recognizer   = None
_label_to_name = _load_label_to_name()

# Rolling prediction buffer for stable overlay status
_pred_buffer   = deque(maxlen=3)
_pred_buf_lock = threading.Lock()

def _load_recognizer():
    global _recognizer, _label_to_name
    if os.path.exists(FACE_MODEL):
        try:
            r = cv2.face.LBPHFaceRecognizer_create()
            r.read(FACE_MODEL)
            _recognizer = r
            _label_to_name = _load_label_to_name()
            names = list(_label_to_name.values())
            print(f"[OVERLAY] face model loaded with {len(names)} person(s): {', '.join(names)} ✅")
        except Exception as e:
            print(f"[OVERLAY] could not load face model: {e}")

_load_recognizer()

_status      = LABEL_TEXT
_status_lock = threading.Lock()
def _set_status(s):
    global _status
    with _status_lock: _status = s
def _get_status():
    with _status_lock: return _status

def _get_dynamic_threshold(brightness: float, sharpness: float) -> float:
    """Mirror of webcam_recognition's adaptive threshold logic."""
    bright_factor = (_BRIGHT_TARGET - brightness) / _BRIGHT_TARGET
    bright_factor = max(-1.0, min(1.0, bright_factor))
    sharp_factor  = (_SHARPNESS_MIN - sharpness) / _SHARPNESS_MIN
    sharp_factor  = max(-1.0, min(1.0, sharp_factor))
    offset = (bright_factor * 0.6 + sharp_factor * 0.4) * _THRESHOLD_RANGE
    return max(80.0, min(280.0, _BASE_THRESHOLD + offset))


def _check_status(gray):
    if _recognizer is None:
        return "👀"
    face = _get_largest_face(gray)
    if face is None:
        return "no face 👻"

    # ── Reload label→name mapping on each check ────────────
    # This ensures the overlay picks up name changes or new enrollments
    # without needing to restart the overlay window.
    global _label_to_name
    _label_to_name = _load_label_to_name()

    # Estimate lighting for adaptive threshold
    face_roi = gray[face[1]:face[1]+face[3], face[0]:face[0]+face[2]]
    if face_roi.size > 0:
        brightness = float(np.mean(face_roi))
        sharpness  = float(cv2.Laplacian(face_roi, cv2.CV_64F).var())
    else:
        brightness, sharpness = _BRIGHT_TARGET, _SHARPNESS_MIN

    dynamic_threshold = _get_dynamic_threshold(brightness, sharpness)

    (x, y, w, h) = face
    roi          = cv2.resize(gray[y:y+h, x:x+w], (100, 100))
    label, conf  = _recognizer.predict(roi)
    print(f"[OVERLAY] label={label} conf={conf:.1f} thresh={dynamic_threshold:.0f} bright={brightness:.0f}")

    # Push into rolling buffer — use majority vote for stability
    with _pred_buf_lock:
        _pred_buffer.append((label, conf))
        good = sum(1 for l, c in _pred_buffer if c < dynamic_threshold)
        total = len(_pred_buffer)

    if good >= total / 2:
        # Look up the detected label in the label→name map
        name = _label_to_name.get(label)
        if name:
            return f"hey {name}!! 💕"
        return "👀 known??"
    return "👀 stranger??"

def _centre_crop(bgr):
    h, w = bgr.shape[:2]
    side = min(h, w)
    return bgr[(h-side)//2:(h-side)//2+side, (w-side)//2:(w-side)//2+side]

def _make_circle(bgr, size):
    sq   = _centre_crop(bgr)
    rgb  = cv2.cvtColor(sq, cv2.COLOR_BGR2RGB)
    img  = Image.fromarray(rgb).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size-1, size-1), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(1))
    out  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), mask=mask)
    return out

def _add_border(img, color, width=4):
    draw    = ImageDraw.Draw(img)
    s       = img.width
    r, g, b = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
    for i in range(6, 0, -1):
        draw.ellipse((i,i,s-i-1,s-i-1), outline=(r,g,b,int(50*i/6)), width=1)
    draw.ellipse((0,0,s-1,s-1), outline=color, width=width)
    return img

# ── Smart camera opener ───────────────────────────────────────
def _is_real_frame(frame) -> bool:
    """
    Detect rainbow/MSMF vertical-stripe static vs a real camera frame.

    The old col_std check FAILED because rainbow static has DIFFERENT column
    means (each stripe is a different colour) so col_std was high, same as a
    real frame — the check was blind to it.

    These three checks catch the actual garbage patterns:
      1. Row std  — vertical stripe static has near-identical rows → low row_std
      2. Laplacian variance — blurry/uniform frames fail this
      3. Brightness bounds  — all-black (not ready) or all-white frames rejected

    FIX B27: Added None/empty check for frame before processing.
    """
    if frame is None or frame.size == 0:
        return False
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    if brightness < 5.0 or brightness > 250.0:
        return False
    row_stds = float(np.mean(np.std(gray, axis=1)))
    if row_stds < 12.0:
        return False   # vertical-stripe / rainbow static
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if lap_var < 20.0:
        return False   # blurry / solid-colour garbage
    return True


def _try_open(index: int, backend, backend_name: str, warm_secs: float) -> "cv2.VideoCapture | None":
    """Open camera on one backend, wait warm_secs, validate frames. Returns cap or None."""
    print(f"[OVERLAY] trying backend: {backend_name} (warm-up {warm_secs:.1f}s)")
    cap = None
    try:
        cap = cv2.VideoCapture(index, backend)
    except Exception:
        try:
            cap = cv2.VideoCapture(index)
        except Exception:
            print(f"[OVERLAY]   {backend_name}: both VideoCapture attempts failed")
            return None

    if cap is None or not cap.isOpened():
        print(f"[OVERLAY]   {backend_name}: isOpened=False")
        if cap: cap.release()
        return None

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    # Flush warm-up frames
    time.sleep(warm_secs)
    for _ in range(10):
        cap.read()
    time.sleep(0.2)

    # Validate: need 4/12 real frames
    good = 0
    for _ in range(12):
        ret, frame = cap.read()
        if ret and frame is not None and _is_real_frame(frame):
            good += 1
        time.sleep(0.05)

    if good >= 4:
        print(f"[OVERLAY]   {backend_name}: ✅ good ({good}/12)")
        return cap

    print(f"[OVERLAY]   {backend_name}: ❌ {good}/12 real frames — releasing")
    cap.release()
    return None


def _open_camera(index: int):
    """
    Try DSHOW (best on Windows) with progressively longer warm-ups,
    then fall back to CAP_ANY. MSMF is intentionally excluded — it produces
    the rainbow vertical-stripe static u saw.
    """
    # Pass 1: DSHOW with normal warm-up
    cap = _try_open(index, cv2.CAP_DSHOW, "DSHOW", warm_secs=1.5)
    if cap: return cap

    # Pass 2: DSHOW with longer warm-up (some cameras need 3-4s to stabilise)
    time.sleep(1.0)
    cap = _try_open(index, cv2.CAP_DSHOW, "DSHOW (long warm-up)", warm_secs=4.0)
    if cap: return cap

    # Pass 3: CAP_ANY (NOT MSMF directly — let OpenCV pick, hopefully DSHOW)
    time.sleep(0.5)
    cap = _try_open(index, cv2.CAP_ANY, "CAP_ANY", warm_secs=3.0)
    if cap: return cap

    print("[OVERLAY] all backends failed — camera unavailable")
    print("[OVERLAY] 💡 tip: run cam_diag.py to find out why")
    return None


# ── Overlay window ────────────────────────────────────────────
class CamOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Jarvis Cam")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", "black")
        self.root.config(bg="black")

        wh = WINDOW_SIZE + 36
        self.root.geometry(f"{WINDOW_SIZE}x{wh}")

        self.canvas = tk.Canvas(self.root, width=WINDOW_SIZE, height=WINDOW_SIZE,
                                bg="black", highlightthickness=0)
        self.canvas.pack()

        self.label_var = tk.StringVar(value=LABEL_TEXT)
        tk.Label(self.root, textvariable=self.label_var,
                 bg="black", fg=BORDER_COLOR,
                 font=("Segoe UI Emoji", 9, "bold")).pack(pady=(0,2))

        self._drag_x = self._drag_y = 0
        self.root.bind("<ButtonPress-1>", lambda e: (setattr(self,"_drag_x",e.x), setattr(self,"_drag_y",e.y)))
        self.root.bind("<B1-Motion>",     self._drag)
        self.root.bind("<ButtonPress-3>", lambda e: self._quit())

        self._position_window()
        self._running    = True
        self._photo      = None
        self._pending    = None
        self._frame_lock = threading.Lock()

        threading.Thread(target=self._cam_loop, daemon=True).start()
        self.root.after(50, self._poll)

    def _position_window(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        ww, wh = WINDOW_SIZE, WINDOW_SIZE + 36
        pos = {
            "top-left":     (MARGIN, MARGIN),
            "top-right":    (sw-ww-MARGIN, MARGIN),
            "bottom-left":  (MARGIN, sh-wh-MARGIN-48),
            "bottom-right": (sw-ww-MARGIN, sh-wh-MARGIN-48),
        }
        x, y = pos.get(CORNER, pos["bottom-right"])
        self.root.geometry(f"{ww}x{wh}+{x}+{y}")

    def _drag(self, e):
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    def _quit(self):
        self._running = False
        self.root.destroy()

    def _cam_loop(self):
        try:
            from webcam_recognition import push_frame as _push
        except Exception:
            _push = None

        cap           = None
        bad_streak    = 0   # consecutive bad reads
        interval      = 1.0 / FPS_TARGET
        check_counter = 0

        while self._running:
            t0 = time.time()

            # ── honour pause request (enroll needs the camera) ─
            if not _cam_paused.is_set():
                if cap is not None:
                    cap.release()
                    cap = None
                    print("[OVERLAY] camera released for enroll 📷")
                    _set_status("⏸ enrolling...")
                _cam_paused.wait()   # block until _resume_camera() is called
                print("[OVERLAY] camera resumed after enroll ✅")

            # ── (re)open camera if needed ─────────────────────
            if cap is None:
                _set_status("📷 connecting...")
                cap = _open_camera(CAM_INDEX)
                if cap is None:
                    _set_status("❌ no camera")
                    time.sleep(3)
                    continue
                bad_streak = 0
                time.sleep(0.2)

            ret, frame = cap.read()

            # ── handle bad frame ──────────────────────────────
            if not ret or frame is None:
                bad_streak += 1
                if bad_streak > 15:
                    print(f"[OVERLAY] {bad_streak} bad reads — releasing and reopening camera")
                    cap.release()
                    cap = None
                    bad_streak = 0
                time.sleep(0.05)
                continue

            # ── validate frame isn't static ───────────────────
            if not _is_real_frame(frame):
                bad_streak += 1
                if bad_streak > 20:
                    print("[OVERLAY] too many static frames — reopening camera")
                    cap.release()
                    cap = None
                    bad_streak = 0
                time.sleep(0.05)
                continue

            bad_streak = 0
            frame = cv2.flip(frame, 1)

            if _push:
                try: _push(frame)
                except Exception: pass

            check_counter += 1
            if check_counter >= 30:
                check_counter = 0
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.equalizeHist(gray)
                _set_status(_check_status(gray))

            circ = _make_circle(frame, WINDOW_SIZE)
            circ = _add_border(circ, BORDER_COLOR, width=4)

            with self._frame_lock:
                self._pending = circ

            time.sleep(max(0, interval - (time.time() - t0)))

        if cap is not None:
            cap.release()

    def _poll(self):
        with self._frame_lock:
            frame, self._pending = self._pending, None
        if frame is not None:
            self._photo = ImageTk.PhotoImage(frame)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.label_var.set(_get_status())
        if self._running:
            self.root.after(50, self._poll)

    def run(self):
        try:    self.root.mainloop()
        finally: self._running = False


# ── Global instance + pause/resume API (used by enroll_face) ─
_overlay_instance = None
_cam_paused       = threading.Event()   # SET = running, CLEAR = paused
_cam_paused.set()


def _pause_camera():
    """Signal the overlay cam_loop to release the camera."""
    _cam_paused.clear()
    print("[OVERLAY] camera pause requested")


def _resume_camera():
    """Signal the overlay cam_loop to reopen the camera."""
    _cam_paused.set()
    print("[OVERLAY] camera resume requested")


def launch_overlay():
    global _overlay_instance
    def _run():
        global _overlay_instance
        _overlay_instance = CamOverlay()
        _overlay_instance.run()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    print("[OVERLAY] starting 💕  right-click to close, drag to move")
    CamOverlay().run()