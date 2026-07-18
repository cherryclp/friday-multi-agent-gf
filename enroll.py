# ============================================================
#  JARVIS FACE ENROLLER — fixed edition 💕
#  Fixes:
#    1. False face detections (door/furniture) → strict filtering
#    2. Only saves the LARGEST face in frame (that's u babe)
#    3. Draws only the selected face box (no confusion)
#    4. Does NOT open camera twice (no static glitch)
# ============================================================

import cv2
import json
import time
import os
import numpy as np
from datetime import datetime

JARVIS_DIR    = os.path.dirname(os.path.abspath(__file__))
FACE_DATA_DIR = os.path.join(JARVIS_DIR, "face_data")
ENROLL_FILE   = os.path.join(FACE_DATA_DIR, "enrolled.json")
MODEL_PATH    = os.path.join(FACE_DATA_DIR, "face_model.yml")
FACE_DB_PATH  = os.path.join(FACE_DATA_DIR, "face_db.json")
SAMPLES_DIR   = os.path.join(FACE_DATA_DIR, "samples")
os.makedirs(FACE_DATA_DIR, exist_ok=True)
os.makedirs(SAMPLES_DIR, exist_ok=True)

N_SAMPLES_EACH = 40   # per pose — more = better
CAM_INDEX      = 0

# Pose guidance for multi-pose enrollment
ENROLL_POSES = [
    ("look straight at camera", 40),
    ("slowly turn ur head LEFT", 30),
    ("slowly turn ur head RIGHT", 30),
    ("tilt chin UP a bit", 20),
    ("tilt chin DOWN a bit", 20),
]

# Only frontal cascade during enroll — alt2 picks up too many false positives
_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

def get_largest_face(gray):
    """
    Detect faces and return ONLY the largest one.
    Strict params to avoid detecting furniture/doors.
    Also rejects any face that is less than 15% of frame width
    (real face sitting at desk will be large).
    """
    h, w = gray.shape
    min_face = max(100, int(w * 0.15))   # must be at least 15% of frame width

    faces = _cascade.detectMultiScale(
        gray,
        scaleFactor=1.08,
        minNeighbors=8,      # high — eliminates false positives like doors
        minSize=(min_face, min_face),
        flags=cv2.CASCADE_SCALE_IMAGE
    )
    if len(faces) == 0:
        return None
    # pick largest by area
    return max(faces, key=lambda f: f[2] * f[3])


def collect_pose(cap, n: int, pose_name: str):
    """Collect aligned face samples for a specific head pose."""
    samples = []
    collected = 0
    stall_counter = 0
    print(f"\n[ENROLL] ── POSE: {pose_name} ({n} samples) ──")
    print(f"[ENROLL] {pose_name} and hold still...")

    while collected < n:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame = cv2.flip(frame, 1)
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray  = cv2.equalizeHist(gray)

        face = get_largest_face(gray)

        # draw all faces faintly
        all_faces = _cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(40,40))
        for (fx, fy, fw, fh) in all_faces:
            cv2.rectangle(frame, (fx, fy), (fx+fw, fy+fh), (60, 60, 60), 1)

        if face is not None:
            aligned = _align_face(gray, face)
            if aligned is not None:
                samples.append(aligned)
            else:
                (x, y, w, h) = face
                samples.append(cv2.resize(gray[y:y+h, x:x+w], (100, 100)))
            collected += 1
            stall_counter = 0

            # draw selected face
            (x, y, w, h) = face
            cv2.rectangle(frame, (x, y), (x+w, y+h), (180, 105, 255), 3)
            cv2.putText(frame, f"{pose_name}: {collected}/{n}", (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 105, 255), 2)
        else:
            stall_counter += 1
            if stall_counter > 15:
                cv2.putText(frame, f"no face — adjust for '{pose_name}'", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 100, 255), 2)

        # status bar
        cv2.putText(frame, f"Enrolling: {pose_name}  {collected}/{n}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 105, 180), 2)

        cv2.imshow("Jarvis Enrollment", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("[ENROLL] Cancelled.")
            return samples, True

        time.sleep(0.04)

    print(f"[ENROLL] Pose '{pose_name}': {collected} samples collected ✅")
    return samples, False


# ── Import shared helpers from webcam_recognition ────────────
from webcam_recognition import (
    _load_face_db,
    _save_face_db,
    _save_samples,
    _rebuild_model,
    _detect_eyes_in_face,
    _align_face,
    _augment_sample,
)


def main():
    # ── 🔐 PASSWORD GATE ─────────────────────────────────────
    from password_gate import attempt_unlock, MAX_ATTEMPTS
    import getpass as _getpass  # FIX B20: use getpass to hide password input
    print("=" * 45)
    print("  🔐 JARVIS ENROLLMENT — PASSWORD REQUIRED")
    print("=" * 45)
    unlocked = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        pw = _getpass.getpass(f"  Enter password (try {attempt}/{MAX_ATTEMPTS}): ").strip()
        if attempt_unlock(pw):
            print("  ✅ correct!! proceeding babe 💕\n")
            unlocked = True
            break
        else:
            remaining = MAX_ATTEMPTS - attempt
            if remaining > 0:
                print(f"  ❌ wrong password!! {remaining} tr{'y' if remaining == 1 else 'ies'} left\n")
            else:
                print("  ❌ wrong password!! locked out 🔒 enroll cancelled.")
    if not unlocked:
        return
    # ─────────────────────────────────────────────────────────

    # get name — FIX B21: use dynamic default based on system username
    import getpass as _gp
    _default_name = _gp.getuser() or "User"
    if os.path.exists(ENROLL_FILE):
        try:
            with open(ENROLL_FILE) as f:
                _default_name = json.load(f).get("name", _default_name)
        except Exception:
            pass
    entered = input(f"Enter ur name [{_default_name}]: ").strip()
    name = entered if entered else _default_name

    # ── Pause overlay if it's running so we don't fight for the camera ──
    overlay_was_running = False
    try:
        from webcam_overlay import _pause_camera, _resume_camera
        _pause_camera()
        overlay_was_running = True
        print("\n[ENROLL] overlay camera paused — waiting 2s for it to release...")
        time.sleep(2.0)
    except ImportError:
        pass
    except Exception as e:
        print(f"[ENROLL] couldn't pause overlay (that's ok): {e}")

    print("\n[ENROLL] Opening camera (DirectShow)...")
    cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    time.sleep(1.0)

    if not cap.isOpened():
        if overlay_was_running:
            try:
                from webcam_overlay import _resume_camera
                _resume_camera()
            except Exception:
                pass
        print("[ENROLL] ERROR: can't open webcam 😭")
        return

    all_samples = []

    # ── Multi-pose enrollment ──────────────────────────────────
    print("\n[ENROLL] ====== MULTI-POSE ENROLLMENT ======")
    print("[ENROLL] I'll guide u through 5 head positions for robust recognition!")
    print("[ENROLL] Press Q anytime to cancel.")

    cancelled = False
    for pose_name, pose_count in ENROLL_POSES:
        input(f"\n[ENROLL] Ready for '{pose_name}'? Press ENTER to start...")
        samples, cancelled = collect_pose(cap, pose_count, pose_name)
        all_samples.extend(samples)
        if cancelled:
            break

    cap.release()
    cv2.destroyAllWindows()

    # ── Resume overlay ────────────────────────────────────────
    if overlay_was_running:
        try:
            from webcam_overlay import _resume_camera
            time.sleep(0.5)
            _resume_camera()
            print("[ENROLL] overlay camera resumed ✅")
        except Exception as e:
            print(f"[ENROLL] couldn't resume overlay: {e}")

    if cancelled:
        print("\n[ENROLL] Enrollment cancelled by user.")
        return

    raw_count = len(all_samples)
    print(f"\n[ENROLL] Raw samples collected: {raw_count}")

    if raw_count < 20:
        print(f"[ENROLL] Not enough samples ({raw_count}) — need at least 20 😭")
        return

    # ── Augment samples ───────────────────────────────────────
    print("[ENROLL] Augmenting samples (flip + rotation)...")
    augmented = []
    for s in all_samples:
        augmented.extend(_augment_sample(s))
    all_samples = augmented
    total = len(all_samples)
    print(f"[ENROLL] Augmented to {total} total samples ✅")

    # ── Add to multi-person face database ──────────────────────
    db = _load_face_db()

    if name in db["persons"]:
        label = db["persons"][name]["label"]
        print(f"[ENROLL] re-enrolling '{name}' with existing label {label}")
    else:
        label = db["next_label"]
        db["next_label"] += 1
        db["persons"][name] = {"label": label, "sample_count": 0}
        print(f"[ENROLL] new person '{name}' → label {label}")

    # Save this person's samples, then rebuild the model from ALL persons
    _save_samples(name, all_samples)
    db["persons"][name]["sample_count"] = total
    _save_face_db(db)

    recognizer = _rebuild_model(db)
    if recognizer is None:
        print(f"[ENROLL] something went wrong training the model 😭")
        return

    persons_list = ', '.join(db['persons'].keys())
    print(f"[ENROLL] ✅ Model saved! {total} samples (5 poses, augmented) for '{name}'")
    print(f"[ENROLL] ✅ Now recognises: {persons_list}")
    print(f"[ENROLL] Restart jarvis.py or it'll auto-pick up the new model 💕")


if __name__ == "__main__":
    main()