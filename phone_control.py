# ============================================================
#  JARVIS PHONE CONTROL 📱
#  Controls your Android phone via ADB (USB or WiFi).
#
#  SETUP:
#    1. Install ADB:  https://developer.android.com/studio/releases/platform-tools
#       (download platform-tools zip, extract somewhere, add to PATH)
#       OR just drop adb.exe next to jarvis.py
#
#    2. Enable Developer Options on phone:
#       Settings → About Phone → tap "Build Number" 7 times
#
#    3. Enable USB Debugging:
#       Settings → Developer Options → USB Debugging → ON
#
#    4. Plug in phone via USB. Accept the "Allow USB Debugging" popup on phone.
#
#    5. (Optional) For WiFi control after initial USB setup:
#       Run:  python phone_control.py --setup-wifi
#       Then unplug USB — Jarvis controls ur phone over WiFi now 💕
#
#  HOW JARVIS USES IT:
#    "open youtube on my phone"
#    "send a whatsapp to [contact] on my phone"
#    "take a screenshot of my phone"
#    "turn up the volume on my phone"
#    "lock my phone"
#    "what's on my phone screen"
#    "open settings on my phone"
#    "call [contact] on my phone"
# ============================================================

import subprocess
import os
import time
import threading
import json
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
os.environ["PYTHONUTF8"] = "1"
import re
import shutil
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────
# ADB is auto-detected: PATH first, then local adb_files/, then common installs.
# Override via ADB_PATH environment variable if needed.

JARVIS_DIR   = os.path.dirname(os.path.abspath(__file__))
WIFI_CFG     = os.path.join(JARVIS_DIR, "phone_wifi.json")

# ── ADB binary auto-detection ─────────────────────────────────
_ADB_CACHE = None

def _find_adb() -> str | None:
    """
    Locate the adb executable. Resolution order:
      1. ADB_PATH environment variable (manual override)
      2. 'adb' in system PATH (most portable)
      3. Local adb_files/platform-tools/ relative to this script
      4. Common Windows install locations
    Returns the full path to adb, or None if not found.
    """
    global _ADB_CACHE
    if _ADB_CACHE is not None:
        return _ADB_CACHE

    candidates = []

    # 1. Env var override
    env_path = os.environ.get("ADB_PATH", "").strip()
    if env_path:
        candidates.append(env_path)

    # 2. PATH
    path_adb = shutil.which("adb")
    if path_adb:
        candidates.append(path_adb)

    # 3. Local adb_files/ directory
    local_paths = [
        os.path.join(JARVIS_DIR, "adb_files", "platform-tools", "adb.exe"),
        os.path.join(JARVIS_DIR, "adb_files", "platform-tools", "adb"),
        os.path.join(JARVIS_DIR, "adb", "adb.exe"),
    ]
    candidates.extend(local_paths)

    # 4. Common Windows install locations
    common_win = [
        r"C:\platform-tools\adb.exe",
        r"C:\Program Files\platform-tools\adb.exe",
        r"C:\Program Files (x86)\platform-tools\adb.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "platform-tools", "adb.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Android", "platform-tools", "adb.exe"),
    ]
    candidates.extend(common_win)

    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            _ADB_CACHE = path
            print(f"[ADB] found at: {path}")
            return path
        # Also try with .exe on Windows
        if path and not path.endswith(".exe"):
            exe_path = path + ".exe"
            if os.path.isfile(exe_path) and os.access(exe_path, os.X_OK):
                _ADB_CACHE = exe_path
                print(f"[ADB] found at: {exe_path}")
                return exe_path

    print("[ADB] not found — check PATH or set ADB_PATH env var")
    return None


def check_adb_available() -> str | None:
    """
    Check that the ADB binary exists and is runnable.
    Returns None if OK, or an error message string if unavailable.
    """
    adb_path = _find_adb()
    if adb_path is None:
        return (
            "❌ ADB not found babe 😭\n"
            "Download Android Platform Tools from:\n"
            "  https://developer.android.com/studio/releases/platform-tools\n\n"
            "Then either:\n"
            "  1. Extract and add the folder to your PATH, OR\n"
            "  2. Drop the platform-tools folder into this project's adb_files/ directory, OR\n"
            "  3. Set the ADB_PATH environment variable to point to adb.exe\n"
            "     e.g. set ADB_PATH=C:\\platform-tools\\adb.exe"
        )
    # Verify it actually runs
    try:
        result = subprocess.run(
            [adb_path, "version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return f"❌ ADB binary found but won't run: {result.stderr.strip()}"
        print(f"[ADB] version: {result.stdout.splitlines()[0] if result.stdout else 'unknown'}")
    except Exception as e:
        return f"❌ ADB binary found but failed to execute: {e}"
    return None  # All good

# Screenshot save location
SCREENSHOT_DIR = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Pictures", "JarvisPhone")

# ── KEYCODES (Android) ────────────────────────────────────────
KEYCODE = {
    "home":        3,
    "back":        4,
    "volume_up":   24,
    "volume_down": 25,
    "power":       26,
    "camera":      27,
    "call":        5,
    "end_call":    6,
    "mute":        91,
    "media_play":  85,
    "media_next":  87,
    "media_prev":  88,
    "brightness_up":   221,
    "brightness_down": 220,
    "notification_bar": None,  # handled via swipe
    "screenshot":  None,       # handled via key combo
    "recent_apps": 187,
    "flashlight":  None,       # handled via quick settings tile
}

# ── Android app package names ─────────────────────────────────
APP_PACKAGES = {
    "youtube":     "com.google.android.youtube",
    "spotify":     "com.spotify.music",
    "whatsapp":    "com.whatsapp",
    "instagram":   "com.instagram.android",
    "twitter":     "com.twitter.android",
    "x":           "com.twitter.android",
    "reddit":      "com.reddit.frontpage",
    "netflix":     "com.netflix.mediaclient",
    "maps":        "com.google.android.apps.maps",
    "chrome":      "com.android.chrome",
    "gmail":       "com.google.android.gm",
    "calendar":    "com.google.android.calendar",
    "camera":      "com.android.camera2",
    "photos":      "com.google.android.apps.photos",
    "files":       "com.google.android.documentsui",
    "settings":    "com.android.settings",
    "phone":       "com.android.dialer",
    "dialer":      "com.android.dialer",
    "contacts":    "com.android.contacts",
    "messages":    "com.google.android.apps.messaging",
    "play store":  "com.android.vending",
    "clock":       "com.google.android.deskclock",
    "calculator":  "com.google.android.calculator",
    "discord":     "com.discord",
    "telegram":    "org.telegram.messenger",
    "snapchat":    "com.snapchat.android",
    "tiktok":      "com.zhiliaoapp.musically",
    "amazon":      "com.amazon.mShop.android.shopping",
    "facebook":    "com.facebook.katana",
    "linkedin":    "com.linkedin.android",
    "zoom":        "us.zoom.videomeetings",
    "teams":       "com.microsoft.teams",
    "uber":        "com.ubercab",
    "swiggy":      "in.swiggy.android",
    "zomato":      "app.zomato.com",
    "gpay":        "com.google.android.apps.nbu.paisa.user",
    "paytm":       "net.one97.paytm",
    "phonepe":     "com.phonepe.app",
    "myntra":      "com.myntra.android",
    "flipkart":    "com.flipkart.android",
}


# ── Core ADB runner ───────────────────────────────────────────
def _get_adb_path() -> str:
    """Get the resolved ADB path, raising a clear error if not found."""
    adb = _find_adb()
    if adb is None:
        raise RuntimeError(
            "ADB not found. Check PATH, set ADB_PATH env var, or drop "
            "platform-tools into the adb_files/ folder."
        )
    return adb


def _adb(*args, timeout: int = 10) -> tuple[bool, str]:
    """
    Run an adb command. Returns (success: bool, output: str).
    Automatically uses WiFi device if configured.
    """
    try:
        adb_path = _get_adb_path()
    except RuntimeError as e:
        return False, f"ERROR: {e}"

    cmd = [adb_path]
    # If WiFi is configured, target that device explicitly
    # ── Target the active device ────────────────────────────
    target = _load_active_target()
    if target:
        cmd += ["-s", target]
    cmd += list(args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output = result.stdout.strip()
        ok = result.returncode == 0
        if not ok and result.stderr.strip():
            output = result.stderr.strip()

        # ── Auto-reconnect on connection loss ────────────────
        if not ok and target and _is_connection_error(output):
            if _auto_reconnect_wifi():
                time.sleep(1)
                # Retry the command once after reconnect
                active_name = _get_active_device_name()
                print(f"[ADB] retrying command on '{active_name}' after reconnect")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                output = result.stdout.strip()
                ok = result.returncode == 0
                if not ok and result.stderr.strip():
                    output = result.stderr.strip()

        return ok, output
    except FileNotFoundError:
        # Clear cache and retry once in case adb was installed between calls
        global _ADB_CACHE
        _ADB_CACHE = None
        return False, (
            "ERROR: adb not found!! install Android Platform Tools and add to PATH babe 😭\n"
            "Download: https://developer.android.com/studio/releases/platform-tools"
        )
    except subprocess.TimeoutExpired:
        return False, "ERROR: adb command timed out — is ur phone connected??"
    except Exception as e:
        return False, f"ERROR: {str(e)}"


# ── FIX B10: Helper for ADB commands that need shell pipes (|, &&, grep) ──
def _adb_shell_pipe(shell_cmd: str, filter_term: str = "", timeout: int = 15) -> tuple[bool, str]:
    """
    Run an ADB shell command with a pipe to grep.
    adb shell passes the entire string to the Android device's shell,
    so pipes and grep work natively.
    Example: _adb_shell_pipe("pm list packages", "youtube")
    Returns:
      (True, stdout) on match found
      (False, "") when grep finds no match (exit code 1)
      (False, error) on actual failure
    """
    try:
        adb_path = _get_adb_path()
    except RuntimeError as e:
        return False, str(e)

    # ── Target the active device ────────────────────────────
    target = _load_active_target()
    cmd = [adb_path]
    if target:
        cmd += ["-s", target]
    shell_full = f"{shell_cmd} | grep -i {filter_term}" if filter_term else shell_cmd
    cmd += ["shell", shell_full]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        elif result.returncode == 1:
            # grep returned no match — not an error
            return False, ""
        else:
            err_output = result.stderr.strip() or f"exit code {result.returncode}"
            # ── Auto-reconnect on connection loss ────────────
            if target and _is_connection_error(err_output):
                if _auto_reconnect_wifi():
                    time.sleep(1)
                    print(f"[ADB] retrying pipe command after reconnect")
                    result = subprocess.run(
                        cmd,
                        capture_output=True, text=True, timeout=timeout,
                    )
                    if result.returncode == 0:
                        return True, result.stdout.strip()
                    elif result.returncode == 1:
                        return False, ""
                    else:
                        return False, result.stderr.strip() or f"exit code {result.returncode}"
            return False, err_output
    except Exception as e:
        return False, str(e)


# _adb_shell_cmd removed — adb shell natively supports && and | on the device,
# so we just use _adb("shell", "full command string") instead.



# ── Multi-device config ──────────────────────────────────────
_DEFAULT_DEVICE_NAME = "phone"


def _load_devices_config() -> dict:
    """
    Load the multi-device config from phone_wifi.json.
    Auto-migrates old single-target format to new multi-device format.
    Returns {"devices": {name: {serial, model}}, "active": name}.
    """
    default = {"devices": {}, "active": _DEFAULT_DEVICE_NAME}
    if not os.path.exists(WIFI_CFG):
        return default
    try:
        with open(WIFI_CFG) as f:
            data = json.load(f)
        # ── Migrate old format ──
        if "target" in data and "devices" not in data:
            serial = data["target"]
            devices = {_DEFAULT_DEVICE_NAME: {"serial": serial}}
            migrated = {"devices": devices, "active": _DEFAULT_DEVICE_NAME}
            _save_devices_config(migrated)
            return migrated
        return data
    except Exception:
        return default


def _save_devices_config(data: dict):
    """Save the full multi-device config to phone_wifi.json."""
    data["_saved_at"] = datetime.now().isoformat()
    with open(WIFI_CFG, "w") as f:
        json.dump(data, f, indent=2)


def _load_active_target() -> str | None:
    """Return the serial/IP:port of the currently active device."""
    cfg = _load_devices_config()
    active = cfg.get("active")
    if active:
        dev = cfg.get("devices", {}).get(active)
        if dev:
            return dev.get("serial")
    return None


def _get_active_device_name() -> str:
    """Return the name of the currently active device."""
    cfg = _load_devices_config()
    return cfg.get("active", _DEFAULT_DEVICE_NAME)


# ── Auto-reconnect for WiFi ADB ──────────────────────────────
_RECONNECT_LOCK = threading.Lock()
_RECONNECT_ATTEMPTS: int = 0
_MAX_RECONNECT_ATTEMPTS: int = 2  # prevent infinite reconnect loops


def _is_connection_error(output: str) -> bool:
    """Check if an ADB error output indicates a connection loss."""
    lower = output.lower()
    return any(term in lower for term in [
        "device offline",
        "device \'",  # "device 'serial' not found"
        "not found",
        "no devices/emulators found",
        "no devices",
        "not connected",
        "connection refused",
        "cannot connect",
        "transport",
        "closed",
    ])


def _auto_reconnect_wifi() -> bool:
    """
    Try to reconnect to the saved WiFi ADB target.
    Uses a lock to prevent concurrent reconnect attempts.
    Returns True if reconnection succeeded.
    """
    global _RECONNECT_ATTEMPTS

    active_name = _get_active_device_name()
    target = _load_active_target()
    if not target:
        return False

    with _RECONNECT_LOCK:
        if _RECONNECT_ATTEMPTS >= _MAX_RECONNECT_ATTEMPTS:
            print(f"[ADB] max reconnect attempts ({_MAX_RECONNECT_ATTEMPTS}) reached — giving up")
            return False

        _RECONNECT_ATTEMPTS += 1
        print(f"[ADB] '{active_name}' connection lost — attempting reconnect to {target} (attempt {_RECONNECT_ATTEMPTS}/{_MAX_RECONNECT_ATTEMPTS})...")

        try:
            adb_path = _get_adb_path()
            result = subprocess.run(
                [adb_path, "connect", target],
                capture_output=True, text=True, timeout=10
            )
            connected = "connected" in result.stdout.lower() or "already" in result.stdout.lower()
            if connected:
                _RECONNECT_ATTEMPTS = 0  # reset so future drops can retry
                print(f"[ADB] WiFi reconnected to {target} ✅")
                return True
            else:
                print(f"[ADB] WiFi reconnect failed: {result.stdout.strip()}")
                return False
        except Exception as e:
            print(f"[ADB] WiFi reconnect error: {e}")
            return False


# ── Connection helpers ────────────────────────────────────────
def check_phone_connected() -> bool:
    """Returns True if at least one Android device is connected."""
    ok, out = _adb("devices")
    if not ok:
        return False
    lines = [l.strip() for l in out.splitlines() if l.strip() and "List of devices" not in l]
    return any("device" in l and "offline" not in l for l in lines)


def get_connected_devices() -> list[str]:
    """Return list of connected device serials."""
    ok, out = _adb("devices")
    if not ok:
        return []
    devices = []
    for line in out.splitlines():
        line = line.strip()
        if line and "List of devices" not in line and "\tdevice" in line:
            devices.append(line.split("\t")[0])
    return devices

def _get_usb_phone_serials() -> list[str]:
    """Return list of USB-connected device serials."""
    ok, out = _adb("devices")
    if not ok:
        return []
    serials = []
    for line in out.splitlines():
        line = line.strip()
        if line and "List of devices" not in line and "\tdevice" in line:
            serial = line.split("\t")[0]
            # USB serials don't contain colons; WiFi ones do (IP:port)
            if ":" not in serial:
                serials.append(serial)
    return serials


def setup_wifi_adb(serial: str = "", device_name: str = "") -> str:
    """
    Connect ADB over WiFi.
    Phone must be connected via USB first.
    If serial is empty, uses the first (or only) USB-connected phone.
    If device_name is empty, uses the phone model as the name.
    Returns status message.
    """
    # Make sure ADB is available first
    adb_err = check_adb_available()
    if adb_err:
        return adb_err

    # ── Choose which USB device to set up ────────────────────
    if not serial:
        usb_serials = _get_usb_phone_serials()
        if not usb_serials:
            return "no USB phone detected! plug ur phone in via USB first babe 😭"
        serial = usb_serials[0]
        if len(usb_serials) > 1:
            return (
                f"multiple USB phones detected! which one?\n"
                f"run: python phone_control.py --setup-wifi --serial <serial>\n"
                f"devices: {', '.join(usb_serials)}"
            )

    # ── Get the phone's IP by running shell commands directly on it ──
    # Build a targeted ADB command to get IP
    adb_path = _get_adb_path()

    def _targeted_adb(*args) -> tuple[bool, str]:
        cmd = [adb_path, "-s", serial] + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            out = r.stdout.strip()
            return r.returncode == 0, out if out else r.stderr.strip()
        except Exception as e:
            return False, str(e)

    ok, out = _targeted_adb("shell", "ip", "route")
    if not ok:
        return f"couldn't get phone IP: {out}"

    ip_match = re.search(r'src (\d+\.\d+\.\d+\.\d+)', out)
    if not ip_match:
        ok2, out2 = _targeted_adb("shell", "ifconfig", "wlan0")
        ip_match = re.search(r'inet addr:(\d+\.\d+\.\d+\.\d+)', out2)
        if not ip_match:
            ok3, out3 = _targeted_adb("shell", "ip", "addr", "show", "wlan0")
            ip_match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', out3)

    if not ip_match:
        return "couldn't find phone's WiFi IP 😭 make sure ur phone is on WiFi babe"

    phone_ip = ip_match.group(1)
    port = 5555

    # ── Get model name for naming ─────────────────────────────
    if not device_name:
        ok_prop, model_out = _targeted_adb("shell", "getprop", "ro.product.model")
        if ok_prop and model_out.strip():
            device_name = model_out.strip().lower().replace(" ", "-")
        else:
            device_name = f"phone-{serial[-4:]}"

    # Enable TCP/IP mode
    ok, out = _targeted_adb("tcpip", str(port))
    if not ok:
        return f"tcpip mode failed: {out}"

    time.sleep(2)

    # Connect over WiFi
    result = subprocess.run(
        [adb_path, "connect", f"{phone_ip}:{port}"],
        capture_output=True, text=True, timeout=10
    )
    connect_out = result.stdout.strip()

    if "connected" in connect_out.lower() or "already" in connect_out.lower():
        target = f"{phone_ip}:{port}"
        # ── Save to multi-device config ───────────────────────
        cfg = _load_devices_config()
        cfg.setdefault("devices", {})[device_name] = {"serial": target}
        cfg["active"] = device_name
        _save_devices_config(cfg)
        return (
            f"✅ {device_name} WiFi ADB connected!! (IP: {phone_ip}:{port})\n"
            f"u can unplug USB now babe 💕\n"
            f"say 'switch to {device_name}' to make this the active phone~"
        )
    else:
        return f"WiFi connect failed 😭: {connect_out}\nMake sure PC and phone are on the same WiFi network!!"


# ── Screen state ──────────────────────────────────────────────
def is_screen_on() -> bool:
    """Check if phone screen is on.
    FIX B10: Removed broken pipe-to-grep (| and grep aren't shell commands here).
    Using dumpsys power which works on all Android versions.
    """
    ok2, out2 = _adb("shell", "dumpsys", "power")
    return "mInteractive=true" in out2 or "Display Power: state=ON" in out2


def wake_screen() -> str:
    """Wake the phone screen if it's off."""
    ok, out = _adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    return "📱 screen woken up!!" if ok else f"ERROR: {out}"


def lock_phone() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["power"]))
    return "🔒 phone locked!!" if ok else f"ERROR: {out}"


# ── Volume / media ────────────────────────────────────────────
def phone_volume_up(steps: int = 3) -> str:
    for _ in range(steps):
        _adb("shell", "input", "keyevent", str(KEYCODE["volume_up"]))
        time.sleep(0.1)
    return f"🔊 volume up {steps} steps on ur phone!!"


def phone_volume_down(steps: int = 3) -> str:
    for _ in range(steps):
        _adb("shell", "input", "keyevent", str(KEYCODE["volume_down"]))
        time.sleep(0.1)
    return f"🔉 volume down {steps} steps!!"


def phone_media_play_pause() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["media_play"]))
    return "⏯ play/pause toggled on phone!!" if ok else f"ERROR: {out}"


def phone_media_next() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["media_next"]))
    return "⏭ next track!!" if ok else f"ERROR: {out}"


def phone_media_prev() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["media_prev"]))
    return "⏮ previous track!!" if ok else f"ERROR: {out}"


# ── App launching ─────────────────────────────────────────────
def open_phone_app(app_name: str) -> str:
    name_lower = app_name.lower().strip()

    pkg = APP_PACKAGES.get(name_lower)
    if not pkg:
        for k, v in APP_PACKAGES.items():
            if k in name_lower or name_lower in k:
                pkg = v
                break

    if pkg:
        # Try monkey launcher first
        ok, out = _adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
        if ok and "error" not in out.lower():
            return f"📱 opened {app_name} on ur phone!!"
        
        # Fallback 1: honeycomb shell (YouTube specific)
        ok2, out2 = _adb("shell", "am", "start",
            "-n", f"{pkg}/{pkg}.app.honeycomb.Shell",
            "--activity-single-top")
        if ok2:
            return f"📱 opened {app_name} on ur phone!!"
        
        # Fallback 2: generic intent
        ok3, out3 = _adb("shell", "am", "start",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-p", pkg)
        if ok3:
            return f"📱 opened {app_name} on ur phone!!"

    # Try searching installed packages — FIX B10: use shell=True for pipe commands
    ok, out = _adb_shell_pipe("pm list packages", name_lower)
    if ok and out:
        found_pkg = out.strip().replace("package:", "").split("\n")[0].strip()
        if found_pkg:
            _adb("shell", "monkey", "-p", found_pkg, "-c", "android.intent.category.LAUNCHER", "1")
            return f"📱 opened {found_pkg} on ur phone!! (best match for '{app_name}')"

    return f"couldn't find '{app_name}' on ur phone 😭 is it installed??"


def go_home() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["home"]))
    return "🏠 went to home screen!!" if ok else f"ERROR: {out}"


def go_back() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["back"]))
    return "◀ went back!!" if ok else f"ERROR: {out}"


def open_recent_apps() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["recent_apps"]))
    return "📱 opened recent apps!!" if ok else f"ERROR: {out}"


# ── Notification panel ────────────────────────────────────────
def pull_down_notifications() -> str:
    """Swipe down to open notification panel."""
    ok, out = _adb("shell", "cmd", "statusbar", "expand-notifications")
    return "🔔 opened notification panel!!" if ok else f"ERROR: {out}"


def close_notifications() -> str:
    ok, out = _adb("shell", "cmd", "statusbar", "collapse")
    return "🔔 closed notifications!!" if ok else f"ERROR: {out}"


# ── Screenshot ────────────────────────────────────────────────
def phone_screenshot(save_locally: bool = True) -> str:
    """
    Take a screenshot on the phone and optionally pull it to PC.
    Returns path to saved file or error string.
    """
    phone_path = "/sdcard/jarvis_screenshot.png"

    # Take screenshot on phone
    ok, out = _adb("shell", "screencap", "-p", phone_path)
    if not ok:
        return f"ERROR: screenshot failed: {out}"

    if not save_locally:
        return f"📸 screenshot saved on phone at {phone_path}!!"

    # Pull to PC
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    local_path = os.path.join(SCREENSHOT_DIR, f"phone_{ts}.png")

    ok2, out2 = _adb("pull", phone_path, local_path)
    if ok2 or os.path.exists(local_path):
        # Clean up from phone
        _adb("shell", "rm", phone_path)
        return f"📸 phone screenshot saved to:\n{local_path}"

    return f"ERROR: couldn't pull screenshot to PC: {out2}"


# ── Text / typing ─────────────────────────────────────────────
def phone_type_text(text: str) -> str:
    """Type text on the phone (whatever field is focused)."""
    # ADB input text doesn't handle special chars well — escape spaces
    safe_text = text.replace(" ", "%s").replace("'", "\\'")
    ok, out = _adb("shell", "input", "text", safe_text)
    return f"⌨️ typed on phone: '{text}'" if ok else f"ERROR: {out}"


def phone_tap(x: int, y: int) -> str:
    """Tap a specific coordinate on the phone screen."""
    ok, out = _adb("shell", "input", "tap", str(x), str(y))
    return f"👆 tapped ({x}, {y}) on phone!!" if ok else f"ERROR: {out}"


def phone_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
    ok, out = _adb("shell", "input", "swipe",
                   str(x1), str(y1), str(x2), str(y2), str(duration_ms))
    return "👆 swiped!!" if ok else f"ERROR: {out}"


def phone_scroll_up() -> str:
    """Scroll up on current screen."""
    return phone_swipe(540, 900, 540, 300, 300)

def phone_scroll_down() -> str:
    """Scroll down on current screen."""
    return phone_swipe(540, 300, 540, 900, 300)


# ── Phone calls ───────────────────────────────────────────────
def make_phone_call(number_or_name: str) -> str:
    """Make a phone call."""
    number = number_or_name.strip()
    # If it looks like a number, dial directly
    if re.match(r'^[\d\+\- \(\)]+$', number):
        clean_number = re.sub(r'[^\d\+]', '', number)
        ok, out = _adb("shell", "am", "start", "-a", "android.intent.action.CALL",
                       "-d", f"tel:{clean_number}")
        return f"📞 calling {number}!!" if ok else f"ERROR: {out}"
    else:
        # Open dialer and search
        _adb("shell", "am", "start", "-a", "android.intent.action.DIAL")
        time.sleep(1.5)
        phone_type_text(number_or_name)
        return f"📞 opened dialer and searched for '{number_or_name}' — confirm the call on ur phone!!"


def end_call() -> str:
    ok, out = _adb("shell", "input", "keyevent", str(KEYCODE["end_call"]))
    return "📵 call ended!!" if ok else f"ERROR: {out}"


# ── WhatsApp on phone ─────────────────────────────────────────
def send_whatsapp_phone(contact_number: str, message: str) -> str:
    """
    Open WhatsApp on phone with a pre-filled message to a number.
    Works WITHOUT the phone being unlocked to a specific contact —
    uses the WhatsApp deep link (wa.me).
    """
    contact_number = re.sub(r'[^\d\+]', '', contact_number)
    if not contact_number.startswith('+'):
        # FIX B11: Don't hardcode +91. Numbers without country code are sent as-is.
        # The user should include the country code in the contact number.
        # E.g., for India: +911234567890, for US: +11234567890
        pass  # leave as-is, user-provided number without + prefix

    encoded_msg = message.replace(' ', '%20').replace('\n', '%0A')
    url = f"https://wa.me/{contact_number.lstrip('+')}?text={encoded_msg}"

    ok, out = _adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
                   "-d", url)
    return (
        f"📱 opened WhatsApp on ur phone with the message ready for {contact_number}!!\n"
        f"just hit Send on ur phone babe 💕"
    ) if ok else f"ERROR: {out}"


def open_whatsapp_chat(contact_name: str) -> str:
    """Open WhatsApp and search for a contact."""
    # Open WhatsApp
    ok, _ = _adb("shell", "monkey", "-p", "com.whatsapp",
                  "-c", "android.intent.category.LAUNCHER", "1")
    if not ok:
        return "couldn't open WhatsApp on phone 😭 is it installed??"
    time.sleep(2)
    # Tap search (usually top right, but it varies — just send Ctrl+F equivalent)
    # WhatsApp search button is usually at the top
    _adb("shell", "input", "keyevent", "84")  # KEYCODE_SEARCH
    time.sleep(0.8)
    phone_type_text(contact_name)
    return f"📱 opened WhatsApp and searched for '{contact_name}' on ur phone!!"


# ── Settings shortcuts ────────────────────────────────────────
def toggle_wifi() -> str:
    """Toggle WiFi on/off."""
    ok, out = _adb("shell", "am", "start", "-a", "android.intent.action.MAIN",
                   "-n", "com.android.settings/.wifi.WifiSettings")
    return "📱 opened WiFi settings on phone!!" if ok else f"ERROR: {out}"


def toggle_bluetooth() -> str:
    ok, out = _adb("shell", "am", "start",
                   "-a", "android.settings.BLUETOOTH_SETTINGS")
    return "📱 opened Bluetooth settings on phone!!" if ok else f"ERROR: {out}"


def open_phone_settings(section: str = "main") -> str:
    section = section.lower()
    settings_map = {
        "main":        "android.settings.SETTINGS",
        "wifi":        "android.settings.WIFI_SETTINGS",
        "bluetooth":   "android.settings.BLUETOOTH_SETTINGS",
        "display":     "android.settings.DISPLAY_SETTINGS",
        "sound":       "android.settings.SOUND_SETTINGS",
        "battery":     "android.intent.action.POWER_USAGE_SUMMARY",
        "apps":        "android.settings.APPLICATION_SETTINGS",
        "storage":     "android.settings.INTERNAL_STORAGE_SETTINGS",
        "accessibility":"android.settings.ACCESSIBILITY_SETTINGS",
    }
    action = settings_map.get(section, "android.settings.SETTINGS")
    ok, out = _adb("shell", "am", "start", "-a", action)
    return f"⚙️ opened {section} settings on phone!!" if ok else f"ERROR: {out}"


# ── Device info ───────────────────────────────────────────────
def get_phone_info() -> str:
    """Get basic info about the connected phone."""
    ok, out = _adb("shell", "getprop")
    if not ok:
        if "not found" in out or "FileNotFoundError" in out:
            return out
        return f"phone not connected 😭: {out}"

    props = {}
    for line in out.splitlines():
        m = re.match(r'\[(.+?)\]: \[(.+?)\]', line)
        if m:
            props[m.group(1)] = m.group(2)

    model   = props.get("ro.product.model", "Unknown")
    brand   = props.get("ro.product.brand", "Unknown")
    android = props.get("ro.build.version.release", "Unknown")
    sdk     = props.get("ro.build.version.sdk", "Unknown")

    # Battery
    ok2, bat = _adb("shell", "dumpsys", "battery")
    battery_pct = "??"
    for line in bat.splitlines():
        if "level:" in line:
            battery_pct = line.split(":")[1].strip()
            break

    return (
        f"📱 Phone: {brand} {model}\n"
        f"Android: {android} (SDK {sdk})\n"
        f"Battery: {battery_pct}%"
    )


def get_phone_battery() -> str:
    ok, out = _adb("shell", "dumpsys", "battery")
    if not ok:
        return f"couldn't check battery 😭: {out}"
    for line in out.splitlines():
        if "level:" in line:
            pct = line.split(":")[1].strip()
            charging = "status: 2" in out  # 2 = charging
            try:
                pct_int = int(pct)
            except (ValueError, TypeError):
                pct_int = 0
            icon = "🔌" if charging else ("🔋" if pct_int > 20 else "🪫")
            return f"{icon} phone battery: {pct}%{' (charging)' if charging else ''}"
    return "couldn't read battery level 😭"


# ── Search on phone ───────────────────────────────────────────
def phone_google_search(query: str) -> str:
    encoded = query.replace(' ', '+')
    url = f"https://www.google.com/search?q={encoded}"
    ok, out = _adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
                   "-d", url)
    return f"🔍 searching '{query}' on ur phone!!" if ok else f"ERROR: {out}"


def open_url_on_phone(url: str) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    ok, out = _adb("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url)
    return f"🌐 opened {url} on ur phone!!" if ok else f"ERROR: {out}"


# ── Screen reading ────────────────────────────────────────────
def get_phone_screen_text() -> str:
    """
    Take a screenshot and try to read what's on it using OCR
    (if tesseract is installed) or just describe the current app.
    """
    # First check what app is in foreground
    ok, out = _adb("shell", "dumpsys", "activity", "activities")
    current_app = "unknown"
    for line in out.splitlines():
        if "mResumedActivity" in line or "topResumedActivity" in line:
            m = re.search(r'([a-z][a-zA-Z0-9.]+)/[.\w]+', line)
            if m:
                current_app = m.group(1)
            break

    # Try to get any focused text
    # adb shell passes the full string to the device's shell, so && works natively
    ok2, content = _adb("shell", "uiautomator dump /sdcard/ui_dump.xml && cat /sdcard/ui_dump.xml")

    if ok2 and "<" in content:
        # Parse some text from the UI dump
        texts = re.findall(r'text="([^"]{3,})"', content)
        texts = [t for t in texts if t and len(t) > 2][:10]
        if texts:
            return (
                f"📱 current app: {current_app}\n"
                f"visible text on screen:\n" + "\n".join(f"• {t}" for t in texts)
            )

    return f"📱 currently open: {current_app} (can't read screen text without UI dump access)"


# ── Torch / flashlight ────────────────────────────────────────
def toggle_flashlight() -> str:
    """Toggle flashlight via camera API (requires API 23+)."""
    # Pull down quick settings, then send back
    pull_down_notifications()
    time.sleep(0.5)
    # Try command-line toggle
    ok, out = _adb("shell", "cmd", "hardware", "flashlight", "on")
    if ok:
        return "🔦 flashlight toggled!!"
    # Fallback: pull down twice for quick settings, tap flashlight tile
    _adb("shell", "cmd", "statusbar", "expand-settings")
    return "🔦 opened quick settings — tap flashlight to toggle it babe 💕"


# ── Do Not Disturb ────────────────────────────────────────────
def set_dnd(enable: bool) -> str:
    ok, out = _adb("shell", "cmd", "notification", "set_dnd", "on" if enable else "off")
    if not ok:
        # Older API
        ok, out = _adb("shell", "settings", "put", "global",
                       "zen_mode", "1" if enable else "0")
    state = "ON 🔕" if enable else "OFF 🔔"
    return f"Do Not Disturb turned {state}!!" if ok else f"ERROR: {out}"


# ── Multi-device switching ────────────────────────────────────
def phone_switch(device_name: str) -> str:
    """
    Switch the active phone to a named device.
    Returns status message.
    """
    cfg = _load_devices_config()
    devices = cfg.get("devices", {})

    if not devices:
        return "no phones configured yet babe 😭 set up a phone first"

    # Try exact match first, then partial match
    name_lower = device_name.lower().strip()
    matched = None
    for name in devices:
        if name.lower() == name_lower:
            matched = name
            break

    if not matched:
        for name in devices:
            if name_lower in name.lower() or name.lower() in name_lower:
                matched = name
                break

    if not matched:
        available = ", ".join(devices.keys())
        return (
            f"couldn't find '{device_name}' 😭\n"
            f"available devices: {available}\n"
            f"say 'switch to [name]' to switch"
        )

    cfg["active"] = matched
    _save_devices_config(cfg)
    return f"✅ switched to {matched}!! 🎯"


def phone_list_devices() -> str:
    """
    List all configured devices and their connection status.
    """
    cfg = _load_devices_config()
    devices = cfg.get("devices", {})
    active = cfg.get("active")

    if not devices:
        return "no phones configured yet 😭"

    lines = ["📱 PHONES:\n"]
    for name, info in devices.items():
        marker = "🎯 " if name == active else "   "
        serial = info.get("serial", "?")
        conn_type = "WiFi" if ":" in serial else "USB"
        lines.append(f"{marker}{name}  ({serial})  [{conn_type}]")

    # Also show any USB devices not in config
    ok, out = _adb("devices")
    if ok:
        for line in out.splitlines():
            line = line.strip()
            if line and "List of devices" not in line and "\tdevice" in line:
                serial = line.split("\t")[0]
                if serial not in [d.get("serial") for d in devices.values()]:
                    lines.append(f"   {serial}  [USB — not configured]")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  MAIN DISPATCHER — called from jarvis_engine.py
# ═══════════════════════════════════════════════════════════════

def execute_phone_tool(tool_name: str, args: dict) -> str:
    """
    Single entry point for all phone tools.
    Called by jarvis_engine's execute_system_tool when tool_name starts with 'phone_'.
    """
    # Check ADB is available first
    adb_err = check_adb_available()
    if adb_err:
        return adb_err

    t = tool_name.lower()

    # phone_switch and phone_list work even when the active device is disconnected
    if t == "phone_switch":
        return phone_switch(args.get("device", args.get("name", "")))
    elif t == "phone_list":
        return phone_list_devices()

    if not check_phone_connected():
        # ── Auto-reconnect before giving up ────────────────
        target = _load_active_target()
        if target and _auto_reconnect_wifi():
            time.sleep(1)
            if check_phone_connected():
                print("[ADB] device reconnected, proceeding with command ✅")
            else:
                active_name = _get_active_device_name()
                return (
                    f"📱 {active_name} not connected babe 😭\n"
                    "tried reconnecting but it didn't work\n"
                    "make sure:\n"
                    "• phone is on the same WiFi network\n"
                    "• USB Debugging is ON\n"
                    "• the phone isn't sleeping (try waking it)\n\n"
                    "run: python phone_control.py --status  to diagnose"
                )
        else:
            active_name = _get_active_device_name()
            return (
                f"📱 {active_name} not connected babe 😭\n"
                "make sure:\n"
                "• phone is on the same WiFi network\n"
                "• USB Debugging is ON\n"
                "• the phone isn't sleeping (try waking it)\n\n"
                "run: python phone_control.py --status  to diagnose"
            )

    if t == "phone_open_app":
        return open_phone_app(args.get("app", ""))

    elif t == "phone_call":
        return make_phone_call(args.get("number", args.get("contact", "")))

    elif t == "phone_end_call":
        return end_call()

    elif t == "phone_whatsapp":
        return send_whatsapp_phone(
            args.get("number", args.get("contact", "")),
            args.get("message", "")
        )

    elif t == "phone_whatsapp_open":
        return open_whatsapp_chat(args.get("contact", ""))

    elif t == "phone_volume_up":
        steps = int(args.get("steps", 3))
        return phone_volume_up(steps)

    elif t == "phone_volume_down":
        steps = int(args.get("steps", 3))
        return phone_volume_down(steps)

    elif t == "phone_play_pause":
        return phone_media_play_pause()

    elif t == "phone_next_track":
        return phone_media_next()

    elif t == "phone_prev_track":
        return phone_media_prev()

    elif t == "phone_screenshot":
        return phone_screenshot()

    elif t == "phone_lock":
        return lock_phone()

    elif t == "phone_wake":
        return wake_screen()

    elif t == "phone_home":
        return go_home()

    elif t == "phone_back":
        return go_back()

    elif t == "phone_battery":
        return get_phone_battery()

    elif t == "phone_info":
        return get_phone_info()

    elif t == "phone_notifications":
        return pull_down_notifications()

    elif t == "phone_settings":
        section = args.get("section", "main")
        return open_phone_settings(section)

    elif t == "phone_search":
        return phone_google_search(args.get("query", ""))

    elif t == "phone_open_url":
        return open_url_on_phone(args.get("url", ""))

    elif t == "phone_type":
        return phone_type_text(args.get("text", ""))

    elif t == "phone_read_screen":
        return get_phone_screen_text()

    elif t == "phone_scroll_up":
        return phone_scroll_up()

    elif t == "phone_scroll_down":
        return phone_scroll_down()

    elif t == "phone_flashlight":
        return toggle_flashlight()

    elif t == "phone_dnd":
        enable = str(args.get("enable", "true")).lower() in ("true", "on", "yes", "1")
        return set_dnd(enable)

    elif t == "phone_setup_wifi":
        return setup_wifi_adb(
            serial=args.get("serial", ""),
            device_name=args.get("name", "")
        )

    elif t == "phone_status":
        cfg = _load_devices_config()
        active = _get_active_device_name()
        if check_phone_connected():
            devices = get_connected_devices()
            info = get_phone_info()
            return (
                f"✅ {active} connected!! 🎯\n"
                f"{info}\n"
                f"active device: {active}\n"
                f"all devices: {', '.join(cfg.get('devices', {}).keys())}\n"
                f"ADB connected: {', '.join(devices)}"
            )
        return f"❌ {active} not connected"

    return f"unknown phone tool: {tool_name}"


# ── PHONE TOOL LIST (for Jarvis's LLM context) ────────────────
PHONE_TOOL_LIST = (
    "\n"
    "PHONE TOOLS — use these when user mentions 'my phone', 'on my phone', 'phone battery', 'call', etc:\n"
    "[CALL_TOOL: phone_open_app, app=youtube]\n"
    "[CALL_TOOL: phone_open_app, app=whatsapp]\n"
    "[CALL_TOOL: phone_open_app, app=spotify]\n"
    "[CALL_TOOL: phone_open_app, app=settings]\n"
    "[CALL_TOOL: phone_call, number=+911234567890]\n"
    "[CALL_TOOL: phone_call, contact=Mom]\n"
    "[CALL_TOOL: phone_end_call]\n"
    "[CALL_TOOL: phone_whatsapp, number=+911234567890, message=hey!!]\n"
    "[CALL_TOOL: phone_volume_up, steps=3]\n"
    "[CALL_TOOL: phone_volume_down, steps=3]\n"
    "[CALL_TOOL: phone_play_pause]\n"
    "[CALL_TOOL: phone_next_track]\n"
    "[CALL_TOOL: phone_prev_track]\n"
    "[CALL_TOOL: phone_screenshot]\n"
    "[CALL_TOOL: phone_lock]\n"
    "[CALL_TOOL: phone_wake]\n"
    "[CALL_TOOL: phone_home]\n"
    "[CALL_TOOL: phone_back]\n"
    "[CALL_TOOL: phone_battery]\n"
    "[CALL_TOOL: phone_info]\n"
    "[CALL_TOOL: phone_notifications]\n"
    "[CALL_TOOL: phone_settings, section=wifi]\n"
    "[CALL_TOOL: phone_search, query=weather today]\n"
    "[CALL_TOOL: phone_open_url, url=https://youtube.com]\n"
    "[CALL_TOOL: phone_scroll_up]\n"
    "[CALL_TOOL: phone_scroll_down]\n"
    "[CALL_TOOL: phone_flashlight]\n"
    "[CALL_TOOL: phone_dnd, enable=true]\n"
    "[CALL_TOOL: phone_switch, device=v2511]  # switch active phone by name\n"
    "[CALL_TOOL: phone_switch, device=oppo-cph2381]\n"
    "[CALL_TOOL: phone_list]  # list all configured phones\n"
    "[CALL_TOOL: phone_status]\n"
    "\n"
    "PHONE RULES:\n"
    "- The user can say things like 'switch to my vivo' or 'use my OPPO' — use phone_switch.\n"
    "- ALWAYS use phone_ tools when user says anything about their phone.\n"
    "- phone_battery MUST be called every single time — NEVER guess or use remembered values.\n"
    "- phone_open_app opens apps on the PHONE not the PC.\n"
    "- phone_call and phone_whatsapp require password confirmation.\n"
)


# ── CLI helpers ───────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if "--status" in sys.argv:
        print("Checking phone connection...")
        if check_phone_connected():
            devices = get_connected_devices()
            print(f"✅ Connected! Devices: {devices}")
            print(get_phone_info())
        else:
            print("❌ No phone detected.")
            print("Make sure:")
            print("  1. USB Debugging is ON in Developer Options")
            print("  2. Phone is plugged in (or WiFi ADB configured)")
            print("  3. You accepted the 'Allow USB Debugging' popup")

    elif "--setup-wifi" in sys.argv:
        print("Setting up WiFi ADB...")
        print("Make sure phone is connected via USB first!")
        result = setup_wifi_adb()
        print(result)

    elif "--screenshot" in sys.argv:
        print("Taking phone screenshot...")
        result = phone_screenshot()
        print(result)

    elif "--info" in sys.argv:
        print(get_phone_info())

    elif "--battery" in sys.argv:
        print(get_phone_battery())

    else:
        print("JARVIS PHONE CONTROL 📱")
        print("Usage:")
        print("  python phone_control.py --status       check if phone connected")
        print("  python phone_control.py --setup-wifi   set up WiFi ADB (USB required first)")
        print("  python phone_control.py --screenshot   take a phone screenshot")
        print("  python phone_control.py --info         show phone model/android version")
        print("  python phone_control.py --battery      check battery level")
