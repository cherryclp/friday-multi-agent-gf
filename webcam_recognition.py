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
FACE_DB_PATH  = os.path.join(FACE_DATA_DIR, "face_db.json")
SAMPLES_DIR   = os.path.join(FACE_DATA_DIR, "samples")
os.makedirs(FACE_DATA_DIR, exist_ok=True)
os.makedirs(SAMPLES_DIR, exist_ok=True)

# ── Cascades ──────────────────────────────────────────────────
_frontal = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_alt2     = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
_eye_casc  = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
_eye_glass = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye_tree_eyeglasses.xml")

# ── State ─────────────────────────────────────────────────────
_recognizer      = None
_enrolled_name   = None
_label_to_name   = {}   # int label → str name
_recognition_cb  = None
_last_seen_time  = 0
_last_stranger_t = 0
# FIX B29: Use RLock (reentrant lock) instead of Lock.
# recognise_once() and _recognition_loop() can both try to acquire this,
# and a timeout on one shouldn't leave the system in a deadlock state.
# RLock allows the same thread to re-acquire without deadlocking.
_cam_lock        = threading.RLock()

RECOGNITION_COOLDOWN = 300
STRANGER_COOLDOWN    = 120
FRAMES_TO_AVERAGE    = 5
ENROLL_SAMPLES       = 80

# ── Adaptive threshold config ─────────────────────────────────
_BASE_THRESHOLD    = 160.0   # base confidence; adjusted dynamically by lighting
_BRIGHT_TARGET     = 120   # ideal mean brightness for gray face ROI
_THRESHOLD_RANGE   = 60    # how much the threshold can shift (±30 from base)
_SHARPNESS_MIN     = 30    # minimum Laplacian variance to consider a face sharp

# ── Pose guidance strings for multi-pose enrollment ──────────
ENROLL_POSES = [
    ("look straight at camera", 35),
    ("slowly turn ur head LEFT", 25),
    ("slowly turn ur head RIGHT", 25),
    ("tilt chin UP a bit", 15),
    ("tilt chin DOWN a bit", 15),
]

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
    min_sz = max(100, int(w * 0.15))   # match enrollment (15% of frame width)

    faces = _frontal.detectMultiScale(
        gray, scaleFactor=1.08, minNeighbors=8, minSize=(min_sz, min_sz),
        flags=cv2.CASCADE_SCALE_IMAGE
    )
    if len(faces) == 0:
        faces = _alt2.detectMultiScale(
            gray, scaleFactor=1.08, minNeighbors=5, minSize=(min_sz, min_sz),
            flags=cv2.CASCADE_SCALE_IMAGE
        )
    if len(faces) == 0:
        return None
    return max(faces, key=lambda f: f[2] * f[3])


# ── Face alignment (eye-based) ────────────────────────────────
def _detect_eyes_in_face(gray, face_rect):
    """
    Detect eyes within a face region.
    Returns list of (x, y, w, h) eye rects in full-frame coords, or empty list.
    """
    x, y, w, h = face_rect
    # Expand region slightly for better eye detection
    margin_x = int(w * 0.1)
    margin_y = int(h * 0.15)
    x1 = max(0, x - margin_x)
    y1 = max(0, y - margin_y)
    x2 = min(gray.shape[1], x + w + margin_x)
    y2 = min(gray.shape[0], y + h + margin_y)
    face_roi = gray[y1:y2, x1:x2]

    if face_roi.size == 0:
        return []

    # Try regular eye cascade first, then glasses-friendly version
    eyes = _eye_casc.detectMultiScale(face_roi, scaleFactor=1.08, minNeighbors=4, minSize=(20, 20))
    if len(eyes) < 2:
        eyes = _eye_glass.detectMultiScale(face_roi, scaleFactor=1.08, minNeighbors=4, minSize=(20, 20))

    # Convert coords back to full frame
    result = []
    for (ex, ey, ew, eh) in eyes:
        result.append((x1 + ex, y1 + ey, ew, eh))
    return result


def _align_face(gray, face_rect, target_size: int = 100) -> np.ndarray | None:
    """
    Detect eyes and align face so eyes are horizontal.
    Returns aligned 100x100 face ROI, or None if alignment fails.
    """
    eyes = _detect_eyes_in_face(gray, face_rect)

    if len(eyes) >= 2:
        # Sort eyes by X position to get left and right
        eyes_sorted = sorted(eyes, key=lambda e: e[0])
        (lx, ly, lw, lh) = eyes_sorted[0]
        (rx, ry, rw, rh) = eyes_sorted[-1]

        left_center  = (lx + lw // 2, ly + lh // 2)
        right_center = (rx + rw // 2, ry + rh // 2)

        # Angle between eyes
        dx = right_center[0] - left_center[0]
        dy = right_center[1] - left_center[1]
        angle = np.degrees(np.arctan2(dy, dx))

        # Center of rotation (midpoint between eyes)
        cx = int((left_center[0] + right_center[0]) // 2)
        cy = int((left_center[1] + right_center[1]) // 2)

        # Desired distance between eyes in output (roughly 40% of target_size)
        desired_eye_dist = target_size * 0.4
        current_eye_dist = np.sqrt(dx**2 + dy**2)
        scale = desired_eye_dist / max(current_eye_dist, 1.0)
        scale = min(scale, 1.5)  # don't over-zoom on profile views

        # Build rotation matrix
        M = cv2.getRotationMatrix2D((cx, cy), angle, scale)

        # Apply affine transform, crop to face region
        aligned_size = int(target_size * 1.2)  # slightly larger for cropping
        warped = cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]),
                                flags=cv2.INTER_CUBIC)

        # Extract face region centered on eye midpoint
        half = aligned_size // 2
        x1 = max(0, cx - half)
        y1 = max(0, cy - int(half * 0.85))  # bias a bit upward (forehead)
        x2 = min(warped.shape[1], x1 + aligned_size)
        y2 = min(warped.shape[0], y1 + aligned_size)

        aligned_face = warped[y1:y2, x1:x2]
        if aligned_face.size > 0:
            # Resize to target size
            return cv2.resize(aligned_face, (target_size, target_size))

    # Fallback: basic centered crop
    x, y, w, h = face_rect
    roi = gray[y:y+h, x:x+w]
    if roi.size == 0:
        return None
    return cv2.resize(roi, (target_size, target_size))


# ── Adaptive confidence threshold based on lighting ──────────
def _estimate_lighting(gray, face_rect=None) -> dict:
    """
    Estimate lighting conditions from the full frame or face region.
    Returns dict with 'brightness' (0-255 mean) and 'sharpness' (Laplacian variance).
    """
    if face_rect is not None:
        x, y, w, h = face_rect
        region = gray[y:y+h, x:x+w]
        if region.size > 0:
            gray_sample = region
        else:
            gray_sample = gray
    else:
        gray_sample = gray

    brightness = float(np.mean(gray_sample))
    sharpness  = float(cv2.Laplacian(gray_sample, cv2.CV_64F).var())
    return {"brightness": brightness, "sharpness": sharpness}


def _get_dynamic_threshold(lighting: dict) -> float:
    """
    Compute an adaptive confidence threshold based on lighting.
    Brighter + sharper = stricter threshold (lower = harder to pass).
    Dim + blurry = looser threshold (higher = easier to pass).
    """
    brightness = lighting.get("brightness", _BRIGHT_TARGET)
    sharpness  = lighting.get("sharpness", _SHARPNESS_MIN)

    # Brightness factor: optimal at _BRIGHT_TARGET, looser when darker
    bright_factor = (_BRIGHT_TARGET - brightness) / _BRIGHT_TARGET
    bright_factor = max(-1.0, min(1.0, bright_factor))  # clamp [-1, 1]

    # Sharpness factor: blurrier = higher threshold
    sharp_factor = (_SHARPNESS_MIN - sharpness) / _SHARPNESS_MIN
    sharp_factor = max(-1.0, min(1.0, sharp_factor))

    # Combined offset: higher when dark or blurry, lower when bright and sharp
    offset = (bright_factor * 0.6 + sharp_factor * 0.4) * _THRESHOLD_RANGE

    dynamic_threshold = _BASE_THRESHOLD + offset
    # Clamp to reasonable bounds
    dynamic_threshold = max(80.0, min(280.0, dynamic_threshold))

    return dynamic_threshold


# ── Sample augmentation ───────────────────────────────────────
def _augment_sample(sample: np.ndarray) -> list:
    """
    Generate augmented variations of a face sample.
    Returns list of augmented samples.
    """
    augmented = [sample]
    # Horizontal flip
    flipped = cv2.flip(sample, 1)
    augmented.append(flipped)
    # Small rotations (±5°)
    h, w = sample.shape
    for angle in [-5, 5]:
        M = cv2.getRotationMatrix2D((w//2, h//2), angle, 1.0)
        rotated = cv2.warpAffine(sample, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        augmented.append(rotated)
    return augmented


# ── Face Database (multi-person) ──────────────────────────────
def _load_face_db() -> dict:
    """
    Load the multi-person face database.
    Structure: {"persons": {"name": {"label": 0, "sample_count": 80}}, "next_label": 1}
    Migrates old single-person ENROLL_FILE format automatically.
    """
    if not os.path.exists(FACE_DB_PATH):
        # Migrate from old single-person format
        if os.path.exists(ENROLL_FILE):
            try:
                with open(ENROLL_FILE) as f:
                    old = json.load(f)
                name = old.get("name", "babe")
                db = {
                    "persons": {name: {"label": 0, "sample_count": old.get("samples", 0)}},
                    "next_label": 1
                }
                _save_face_db(db)
                print(f"[VISION] migrated old single-person DB → multi-person for '{name}'")
                return db
            except Exception as e:
                print(f"[VISION] migration error: {e}")
        db = {"persons": {}, "next_label": 0}
        _save_face_db(db)
        return db
    try:
        with open(FACE_DB_PATH) as f:
            return json.load(f)
    except Exception as e:
        print(f"[VISION] _load_face_db error: {e}")
        return {"persons": {}, "next_label": 0}


def _save_face_db(db: dict):
    with open(FACE_DB_PATH, "w") as f:
        json.dump(db, f, indent=2)


def _save_samples(name: str, sample_list: list):
    """Save face samples for a person into their own subdirectory."""
    person_dir = os.path.join(SAMPLES_DIR, name)
    os.makedirs(person_dir, exist_ok=True)
    # Clear old samples
    for fname in os.listdir(person_dir):
        try:
            os.remove(os.path.join(person_dir, fname))
        except Exception:
            pass
    # Save new samples
    for i, s in enumerate(sample_list):
        np.save(os.path.join(person_dir, f"{i}.npy"), s)
    print(f"[VISION] saved {len(sample_list)} samples for '{name}' ✅")


def _load_all_samples() -> tuple:
    """Load ALL samples and labels from ALL enrolled persons."""
    db = _load_face_db()
    all_samples, all_labels = [], []
    for name, info in db["persons"].items():
        person_dir = os.path.join(SAMPLES_DIR, name)
        if not os.path.exists(person_dir):
            print(f"[VISION] no samples dir for '{name}' — skipping")
            continue
        label = info["label"]
        count = 0
        for fname in sorted(os.listdir(person_dir), key=lambda x: int(x.split('.')[0]) if x.split('.')[0].isdigit() else 0):
            try:
                s = np.load(os.path.join(person_dir, fname))
                all_samples.append(s)
                all_labels.append(label)
                count += 1
            except Exception as e:
                print(f"[VISION] error loading sample {fname}: {e}")
        print(f"[VISION] loaded {count} samples for '{name}' (label {label})")
    return all_samples, all_labels


def _rebuild_model(db: dict = None):
    """Rebuild the LBPH model from ALL stored samples across all persons."""
    if db is None:
        db = _load_face_db()
    all_samples, all_labels = _load_all_samples()
    if len(all_samples) < 10:
        print(f"[VISION] not enough samples ({len(all_samples)}) to rebuild model")
        return None
    recognizer = cv2.face.LBPHFaceRecognizer_create(radius=2, neighbors=10, grid_x=8, grid_y=8)
    recognizer.train(all_samples, np.array(all_labels))
    recognizer.save(os.path.join(FACE_DATA_DIR, "face_model.yml"))
    print(f"[VISION] rebuilt model with {len(all_samples)} samples across {len(db['persons'])} person(s) ✅")
    return recognizer


# ── Load model ────────────────────────────────────────────────
def load_enrolled() -> bool:
    global _recognizer, _label_to_name, _enrolled_name
    model_path = os.path.join(FACE_DATA_DIR, "face_model.yml")
    if not os.path.exists(model_path):
        return False
    try:
        db = _load_face_db()
        _label_to_name = {v["label"]: k for k, v in db["persons"].items()}
        r = cv2.face.LBPHFaceRecognizer_create()
        r.read(model_path)
        _recognizer = r
        names = list(db["persons"].keys())
        _enrolled_name = names[0] if names else "babe"
        print(f"[VISION] loaded face model with {len(names)} person(s): {', '.join(names)} ✅")
        return True
    except Exception as e:
        print(f"[VISION] load_enrolled error: {e}")
        return False


# ── Recognition from shared frame buffer ─────────────────────
def _recognise_from_shared() -> str:
    """
    Returns the person's name if recognised with enough confidence,
    or one of: 'not_enrolled', 'no_face', 'stranger'
    Uses face alignment + adaptive confidence threshold.
    """
    if _recognizer is None:
        return "not_enrolled"

    results = []
    lighting_estimates = []

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

        # Use aligned face ROI
        aligned = _align_face(gray, face, target_size=100)
        if aligned is None:
            # Fallback to basic crop
            (x, y, w, h) = face
            aligned = cv2.resize(gray[y:y+h, x:x+w], (100, 100))

        label, conf  = _recognizer.predict(aligned)
        results.append((label, conf))

        # Estimate lighting for adaptive threshold
        lighting = _estimate_lighting(gray, face)
        lighting_estimates.append(lighting)

        time.sleep(0.1)

    if not results:
        return "no_face"

    # Compute dynamic threshold from average lighting
    if lighting_estimates:
        avg_lighting = {
            "brightness": np.mean([l["brightness"] for l in lighting_estimates]),
            "sharpness":  np.mean([l["sharpness"] for l in lighting_estimates]),
        }
    else:
        avg_lighting = {"brightness": _BRIGHT_TARGET, "sharpness": _SHARPNESS_MIN}

    dynamic_threshold = _get_dynamic_threshold(avg_lighting)

    # Count votes per label (only if confidence passes the dynamic threshold)
    label_votes = {}
    for label, conf in results:
        if conf < dynamic_threshold:
            label_votes[label] = label_votes.get(label, 0) + 1

    confs  = [f"{c:.1f}" for _, c in results]
    total_passing = sum(1 for _, c in results if c < dynamic_threshold)
    print(f"[VISION] confs={confs} — {total_passing}/{len(results)} below {dynamic_threshold:.0f} (bright={avg_lighting['brightness']:.0f}, sharp={avg_lighting['sharpness']:.0f})")

    if not label_votes:
        return "stranger"

    # Find the label with most votes, must have majority
    best_label = max(label_votes, key=label_votes.get)
    votes = label_votes[best_label]

    if votes >= len(results) / 2:
        name = _label_to_name.get(best_label, "someone")
        print(f"[VISION] recognised as '{name}' (label {best_label}, {votes}/{len(results)} votes)")
        return name

    return "stranger"


def recognise_once(cam_index: int = 0) -> str:
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
    Multi-person enrollment with multi-pose capture and sample augmentation.
    Guides user through multiple head poses (straight, left, right, up, down)
    and augments samples with flips/rotations for more robust recognition.
    """
    global _recognizer, _enrolled_name, _label_to_name

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

    print(f"[ENROLL] Starting multi-pose enrollment for '{name}' 💪")
    print(f"[ENROLL] I'll guide u through different head positions for better recognition!")

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

    raw_samples = []

    # Multi-pose collection
    for pose_name, pose_count in ENROLL_POSES:
        print(f"\n[ENROLL] ── POSE: {pose_name} ({pose_count} samples) ──")
        pose_collected = 0
        stall_counter = 0

        while pose_collected < pose_count:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            frame = cv2.flip(frame, 1)
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray  = cv2.equalizeHist(gray)

            # Use aligned face for better consistency across poses
            face  = _get_largest_face(gray)
            if face is not None:
                aligned = _align_face(gray, face, target_size=100)
                if aligned is not None:
                    raw_samples.append(aligned)
                    pose_collected += 1
                    stall_counter = 0
                    print(f"[ENROLL] {pose_name}: {pose_collected}/{pose_count}")
                else:
                    # Fallback to basic crop
                    (x, y, w, h) = face
                    roi = cv2.resize(gray[y:y+h, x:x+w], (100, 100))
                    raw_samples.append(roi)
                    pose_collected += 1
                    stall_counter = 0
                    print(f"[ENROLL] {pose_name}: {pose_collected}/{pose_count} (no align)")
            else:
                stall_counter += 1
                if stall_counter > 15:
                    print(f"[ENROLL] no face detected for a while — adjust position for '{pose_name}'")
                    stall_counter = 0

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

    raw_count = len(raw_samples)
    if raw_count < 20:
        return f"only got {raw_count} samples 😭 make sure ur face is visible and well-lit babe"

    # Augment samples: each raw sample → original + flip + rotations = 3x
    print(f"[ENROLL] augmenting {raw_count} raw samples...")
    all_samples = []
    for s in raw_samples:
        all_samples.extend(_augment_sample(s))
    augmented_count = len(all_samples)
    print(f"[ENROLL] augmented to {augmented_count} samples ✅")

    # ── Add to multi-person face database ────────────────────
    db = _load_face_db()

    # Get or assign a label for this person
    if name in db["persons"]:
        label = db["persons"][name]["label"]
        print(f"[ENROLL] re-enrolling '{name}' with existing label {label}")
    else:
        label = db["next_label"]
        db["next_label"] += 1
        db["persons"][name] = {"label": label, "sample_count": 0}
        print(f"[ENROLL] new person '{name}' → label {label}")

    # Save samples to disk
    _save_samples(name, all_samples)
    db["persons"][name]["sample_count"] = augmented_count
    _save_face_db(db)

    # Rebuild model from ALL persons
    recognizer = _rebuild_model(db)
    if recognizer is None:
        return f"something went wrong training the model 😭"

    # Update globals
    _label_to_name = {v["label"]: k for k, v in db["persons"].items()}
    _recognizer    = recognizer
    _enrolled_name = name

    person_count = len(db["persons"])
    names_list = ', '.join(db["persons"].keys())
    return f"✅ enrolled {name} ({augmented_count} samples across 5 poses)!! i can now recognise {person_count} person(s): {names_list} 💕"



# ── check_webcam_now (called from chat) ───────────────────────
def check_webcam_now() -> str:
    if _recognizer is None:
        return "i'm not enrolled yet babe 😭 run: python enroll.py"
    # FIX: this used to acquire _cam_lock and then call recognise_once(), which
    # acquires the same non-reentrant lock again. The inner acquire always timed
    # out, so this ALWAYS returned "no_face". recognise_once() handles the lock.
    result = recognise_once()
    if result == "stranger":
        return _get_stranger_reaction()
    elif result == "no_face":
        return "i can't see anyone rn babe 👀 are u there??"
    elif result == "not_enrolled":
        return "i'm not enrolled yet babe 😭 run: python enroll.py"
    # result is a person's name
    return _get_greeting(result)


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
            if result not in ("stranger", "no_face", "not_enrolled") and now - _last_seen_time > RECOGNITION_COOLDOWN:
                _last_seen_time = now
                try: _recognition_cb(_get_greeting(result))
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