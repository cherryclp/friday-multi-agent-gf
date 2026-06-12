# ============================================================
#  JARVIS HOTKEY SUMMON — fixed edition 💕
#  Fix: win.activate() silently fails on Chrome/eel windows.
#       Now uses win32gui directly which actually works.
# ============================================================

import keyboard
import threading
import time
import subprocess
import os
import sys
import ctypes

try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    HAS_GW = False
    print("[HOTKEY] pygetwindow not found — pip install pygetwindow")

# ── CONFIG ────────────────────────────────────────────────────
SUMMON_HOTKEY = "ctrl+space"
JARVIS_DIR    = os.path.dirname(os.path.abspath(__file__))
JARVIS_SCRIPT = os.path.join(JARVIS_DIR, "jarvis.py")

# Jarvis runs in a Chrome/eel window — add ur actual window title keywords here
# Open jarvis, look at the browser tab title, and add a word from it below
WINDOW_KEYWORDS = ["jarvis", "index.html", "eel", "chrome", "localhost"]
# ─────────────────────────────────────────────────────────────

_jarvis_process = None
_summon_callback = None

def set_summon_callback(fn):
    global _summon_callback
    _summon_callback = fn

def start_hotkey_listener():
    keyboard.add_hotkey(SUMMON_HOTKEY, _on_hotkey, suppress=False)
    print(f"[HOTKEY] listening for {SUMMON_HOTKEY}...")


# ── Win32 force-focus (works where pygetwindow.activate() doesn't) ──
def _force_focus(hwnd):
    """
    Windows blocks SetForegroundWindow unless ur process is already in the
    foreground. The workaround: attach to the foreground thread first.
    """
    try:
        SW_RESTORE = 9
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.05)

        # Attach to foreground thread so Windows allows the focus steal
        fg_thread  = ctypes.windll.user32.GetWindowThreadProcessId(
            ctypes.windll.user32.GetForegroundWindow(), None)
        our_thread = ctypes.windll.kernel32.GetCurrentThreadId()

        if fg_thread != our_thread:
            ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, True)

        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.BringWindowToTop(hwnd)

        if fg_thread != our_thread:
            ctypes.windll.user32.AttachThreadInput(fg_thread, our_thread, False)

        return True
    except Exception as e:
        print(f"[HOTKEY] _force_focus error: {e}")
        return False


# ── Find the Jarvis window ────────────────────────────────────
def _find_jarvis_window():
    """Return (hwnd, title) of the best matching window, or (None, None)."""
    if not HAS_GW:
        return None, None
    try:
        all_titles = gw.getAllTitles()
        # Debug: print all window titles so u can spot the right keyword
        # Uncomment the line below if nothing is found:
        # print("[HOTKEY] open windows:", [t for t in all_titles if t.strip()])
        for title in all_titles:
            t = title.lower()
            if any(k in t for k in WINDOW_KEYWORDS):
                wins = gw.getWindowsWithTitle(title)
                if wins:
                    win = wins[0]
                    hwnd = win._hWnd
                    print(f"[HOTKEY] found window: '{title}' (hwnd={hwnd})")
                    return hwnd, title
    except Exception as e:
        print(f"[HOTKEY] search error: {e}")
    return None, None


def _is_jarvis_running() -> bool:
    try:
        import psutil
        for proc in psutil.process_iter(['cmdline']):
            try:
                cmd = ' '.join(proc.info['cmdline'] or [])
                if 'jarvis.py' in cmd:
                    return True
            except Exception:
                pass
    except ImportError:
        pass
    return False


def _launch_jarvis():
    global _jarvis_process
    print("[HOTKEY] jarvis not running — launching it...")
    try:
        _jarvis_process = subprocess.Popen(
            [sys.executable, JARVIS_SCRIPT],
            cwd=JARVIS_DIR,
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        print("[HOTKEY] launched! waiting for window to appear...")
        for _ in range(30):
            time.sleep(0.5)
            hwnd, title = _find_jarvis_window()
            if hwnd:
                time.sleep(0.5)
                _force_focus(hwnd)
                print(f"[HOTKEY] Jarvis window focused: '{title}' 💕")
                break
    except Exception as e:
        print(f"[HOTKEY] launch error: {e}")


def _summon():
    hwnd, title = _find_jarvis_window()

    if hwnd:
        ok = _force_focus(hwnd)
        if ok:
            print(f"[HOTKEY] Jarvis summoned 💕 ('{title}')")
        else:
            print(f"[HOTKEY] focus failed for '{title}' — try running as admin")
    else:
        # Window not found — print all titles to help debug
        if HAS_GW:
            open_titles = [t for t in gw.getAllTitles() if t.strip()]
            print(f"[HOTKEY] no Jarvis window found. Open windows: {open_titles[:10]}")
            print(f"[HOTKEY] WINDOW_KEYWORDS = {WINDOW_KEYWORDS}")
            print(f"[HOTKEY] Add a matching keyword from the list above to WINDOW_KEYWORDS in this file!")

        if _is_jarvis_running():
            print("[HOTKEY] jarvis.py IS running — window just not found yet, try again in a sec")
        else:
            _launch_jarvis()


def _on_hotkey():
    threading.Thread(target=_summon, daemon=True).start()


def _setup_autostart():
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        script   = os.path.abspath(__file__)
        cmd      = f'"{sys.executable}" "{script}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "JarvisHotkey", 0, winreg.REG_SZ, cmd)
        print("[HOTKEY] added to Windows startup ✅")
    except Exception as e:
        print(f"[HOTKEY] autostart setup failed: {e}")


def _remove_autostart():
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "JarvisHotkey")
        print("[HOTKEY] removed from Windows startup ✅")
    except Exception as e:
        print(f"[HOTKEY] remove autostart failed: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("  JARVIS HOTKEY SUMMON 💕")
    print(f"  Hotkey: {SUMMON_HOTKEY}")
    print("  Press Ctrl+C to stop")
    print("=" * 50)

    if "--autostart" in sys.argv:
        _setup_autostart()
        sys.exit(0)
    if "--remove-autostart" in sys.argv:
        _remove_autostart()
        sys.exit(0)
    if "--list-windows" in sys.argv:
        # Debug helper: just print all open window titles and exit
        if HAS_GW:
            print("Open windows:")
            for t in gw.getAllTitles():
                if t.strip():
                    print(f"  '{t}'")
        sys.exit(0)

    try:
        keyboard.add_hotkey(SUMMON_HOTKEY, _on_hotkey, suppress=False)
        print(f"[HOTKEY] listening for {SUMMON_HOTKEY}...")
        print()
        print("  💡 If Ctrl+Space still doesn't focus the window, run:")
        print("     python hotkey_summon.py --list-windows")
        print("     Then add a word from ur Jarvis window title to WINDOW_KEYWORDS")
        print()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[HOTKEY] stopped")
    except Exception as e:
        print(f"[HOTKEY] error: {e}")
        print("[HOTKEY] try running as administrator!")