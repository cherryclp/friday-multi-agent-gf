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
os.makedirs(FACE_DATA_DIR, exist_ok=True)

N_SAMPLES_EACH = 80   # per phase — more = better
CAM_INDEX      = 0

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


def collect_phase(cap, n: int, phase_name: str):
    samples, labels = [], []
    collected = 0
    skipped   = 0
    print(f"\n[ENROLL] ── {phase_name} ──")
    print(f"[ENROLL] Sit normally, look at camera. Collecting {n} samples...")
    print(f"[ENROLL] Move ur head slightly side to side for variety.")

    while collected < n:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        frame = cv2.flip(frame, 1)
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray  = cv2.equalizeHist(gray)

        face = get_largest_face(gray)

        # draw all faces faintly so u can see what's being detected
        all_faces = _cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(40,40))
        for (fx, fy, fw, fh) in all_faces:
            cv2.rectangle(frame, (fx, fy), (fx+fw, fy+fh), (60, 60, 60), 1)

        if face is not None:
            (x, y, w, h) = face
            roi = cv2.resize(gray[y:y+h, x:x+w], (100, 100))
            samples.append(roi)
            labels.append(0)
            collected += 1

            # draw selected face in pink
            cv2.rectangle(frame, (x, y), (x+w, y+h), (180, 105, 255), 3)
            cv2.putText(frame, f"[YOU] {collected}/{n}", (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (180, 105, 255), 2)
        else:
            skipped += 1
            cv2.putText(frame, "no face detected — come closer", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 100, 255), 2)

        # status bar
        cv2.putText(frame, f"{phase_name}  {collected}/{n}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 105, 180), 2)

        cv2.imshow("Jarvis Enrollment", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("[ENROLL] Cancelled.")
            break

        time.sleep(0.04)

    print(f"[ENROLL] Done: {collected} samples collected ✅")
    return samples, labels


def main():
    # ── 🔐 PASSWORD GATE ─────────────────────────────────────
    from password_gate import attempt_unlock, MAX_ATTEMPTS
    print("=" * 45)
    print("  🔐 JARVIS ENROLLMENT — PASSWORD REQUIRED")
    print("=" * 45)
    unlocked = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        pw = input(f"  Enter password (try {attempt}/{MAX_ATTEMPTS}): ").strip()
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

    # get name
    name = "Charan"
    if os.path.exists(ENROLL_FILE):
        try:
            with open(ENROLL_FILE) as f:
                name = json.load(f).get("name", "Charan")
        except Exception:
            pass
    entered = input(f"Enter ur name [{name}]: ").strip()
    if entered:
        name = entered

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

    all_samples, all_labels = [], []

    # ── Phase 1: without glasses ──────────────────────────────
    input("\n[ENROLL] PHASE 1: Take off glasses if u wear them → then press ENTER...")
    s, l = collect_phase(cap, N_SAMPLES_EACH, "no glasses")
    all_samples += s;  all_labels += l

    # ── Phase 2: with glasses ─────────────────────────────────
    input("\n[ENROLL] PHASE 2: Put glasses on → then press ENTER...")
    s, l = collect_phase(cap, N_SAMPLES_EACH, "with glasses")
    all_samples += s;  all_labels += l

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

    total = len(all_samples)
    print(f"\n[ENROLL] Total samples: {total}")

    if total < 30:
        print(f"[ENROLL] Not enough samples ({total}) — need at least 30 😭")
        return

    print("[ENROLL] Training LBPH model...")
    recognizer = cv2.face.LBPHFaceRecognizer_create(
        radius=2,        # slightly larger radius = more tolerant
        neighbors=10,
        grid_x=8,
        grid_y=8
    )
    recognizer.train(all_samples, np.array(all_labels))
    recognizer.save(MODEL_PATH)

    meta = {
        "name": name,
        "samples": total,
        "enrolled_at": datetime.now().isoformat()
    }
    with open(ENROLL_FILE, "w") as f:
        json.dump(meta, f)

    print(f"[ENROLL] ✅ Model saved! {total} samples for '{name}'")
    print(f"[ENROLL] Now restart jarvis.py — she'll recognise u babe 💕")


if __name__ == "__main__":
    main()