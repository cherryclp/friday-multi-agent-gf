"""
JARVIS CAM DIAGNOSTIC
Run this ALONE (close jarvis.py first) to find out what's wrong.
python cam_diag.py
"""
import cv2
import numpy as np
import time
import subprocess
import sys

CAM_INDEX = 0

def check_processes():
    """Check if any known camera-hogging apps are running."""
    hogs = ["obs64", "obs32", "obs", "Teams", "Discord", "Zoom",
            "WindowsCamera", "SkypeApp", "slack", "chrome", "msedge"]
    print("\n── Checking for apps that might be using the camera ──")
    try:
        out = subprocess.check_output("tasklist", shell=True, text=True, stderr=subprocess.DEVNULL)
        found = []
        for hog in hogs:
            if hog.lower() in out.lower():
                found.append(hog)
        if found:
            print(f"  ⚠️  These might be holding the camera: {', '.join(found)}")
            print("     Try closing them, then re-run this script.")
        else:
            print("  ✅ No obvious camera-hogging apps found.")
    except Exception as e:
        print(f"  (couldn't check processes: {e})")

def test_backend(name, backend, index, warm_up=2.0, n_frames=15):
    print(f"\n── Testing backend: {name} ──")
    try:
        cap = cv2.VideoCapture(index, backend)
    except Exception:
        cap = cv2.VideoCapture(index)

    if not cap.isOpened():
        print(f"  ❌ isOpened=False — camera not accessible on this backend")
        return False

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    print(f"  ⏳ Warming up ({warm_up}s)...")
    time.sleep(warm_up)

    results = []
    for i in range(n_frames):
        ret, frame = cap.read()
        if not ret or frame is None:
            results.append({"ok": False, "reason": "no frame"})
            time.sleep(0.1)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(np.mean(gray))
        row_stds   = float(np.mean(np.std(gray, axis=1)))
        lap_var    = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        col_means  = np.mean(gray, axis=0)
        col_std    = float(np.std(col_means))

        is_real = (
            brightness > 5.0 and brightness < 250.0 and
            row_stds > 12.0 and
            lap_var > 20.0
        )
        results.append({
            "ok": is_real,
            "brightness": round(brightness, 1),
            "row_stds": round(row_stds, 1),
            "lap_var": round(lap_var, 1),
            "col_std": round(col_std, 1),
        })
        time.sleep(0.05)

    cap.release()

    good = sum(1 for r in results if r.get("ok"))
    print(f"  Good frames: {good}/{n_frames}")

    # Print sample diagnostics
    for i, r in enumerate(results[:5]):
        if not r.get("ok"):
            reason = r.get("reason", "")
            if not reason:
                reason = (
                    f"brightness={r['brightness']} "
                    f"row_stds={r['row_stds']} "
                    f"lap_var={r['lap_var']} "
                    f"col_std={r['col_std']}"
                )
            print(f"  frame {i}: ❌  {reason}")
        else:
            print(f"  frame {i}: ✅  brightness={r['brightness']} row_stds={r['row_stds']} lap_var={r['lap_var']}")

    if good >= 4:
        print(f"  🎉 Backend {name} WORKS!")
        return True
    else:
        # Diagnose why
        bad = [r for r in results if not r.get("ok") and "brightness" in r]
        if bad:
            avg_bright  = np.mean([r["brightness"]  for r in bad])
            avg_rowstd  = np.mean([r["row_stds"]    for r in bad])
            avg_lapvar  = np.mean([r["lap_var"]      for r in bad])
            print(f"\n  📊 Bad frame averages:")
            print(f"     brightness = {avg_bright:.1f}  (needs 5–250)")
            print(f"     row_stds   = {avg_rowstd:.1f}  (needs > 12  — low = vertical stripe static)")
            print(f"     lap_var    = {avg_lapvar:.1f}  (needs > 20  — low = blurry/uniform)")

            if avg_rowstd < 12.0:
                print("\n  💡 Diagnosis: VERTICAL STRIPE / RAINBOW STATIC")
                print("     The camera is returning garbage frames.")
                print("     → Another app likely has exclusive camera access.")
                print("     → Or try unplugging and re-plugging the webcam.")
            elif avg_bright < 5.0:
                print("\n  💡 Diagnosis: ALL-BLACK FRAMES")
                print("     Camera is opening but not sending image data.")
                print("     → Try a longer warm-up, or camera may be disabled in privacy settings.")
            elif avg_lapvar < 20.0:
                print("\n  💡 Diagnosis: UNIFORM / BLURRY FRAMES")
                print("     Frames are being read but look like solid colour.")
                print("     → Lens cap? Privacy shutter? Bad driver?")
        return False


def try_show_frame():
    """Last resort: just try to show what the camera is actually sending."""
    print("\n── Attempting to display raw camera output (press Q to quit) ──")
    for backend_name, backend in [("DSHOW", cv2.CAP_DSHOW), ("ANY", cv2.CAP_ANY)]:
        cap = cv2.VideoCapture(CAM_INDEX, backend)
        if not cap.isOpened():
            cap.release()
            continue
        time.sleep(1.5)
        for _ in range(60):
            ret, frame = cap.read()
            if ret and frame is not None:
                cv2.imshow(f"Raw Camera ({backend_name}) — press Q to quit", frame)
            if cv2.waitKey(30) & 0xFF == ord('q'):
                break
        cap.release()
        cv2.destroyAllWindows()
        break


if __name__ == "__main__":
    print("=" * 60)
    print("  JARVIS CAMERA DIAGNOSTIC")
    print("  Make sure jarvis.py is NOT running while you do this!")
    print("=" * 60)

    check_processes()

    worked = False
    worked |= test_backend("DSHOW",   cv2.CAP_DSHOW, CAM_INDEX, warm_up=2.0)
    if not worked:
        worked |= test_backend("CAP_ANY", cv2.CAP_ANY,  CAM_INDEX, warm_up=3.0)

    if not worked:
        print("\n" + "=" * 60)
        print("  ❌ No working backend found.")
        print("  Trying to show raw feed so u can see what the camera is sending...")
        print("=" * 60)
        try_show_frame()
    else:
        print("\n" + "=" * 60)
        print("  ✅ Camera is working! The issue is likely warm-up timing.")
        print("  Copy the new webcam_overlay.py and restart jarvis.py.")
        print("=" * 60)
