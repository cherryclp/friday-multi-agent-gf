# ============================================================
#  JARVIS TOOL REGISTRY
#  Declarative registry replacing the if-elif chain
# ============================================================

from dataclasses import dataclass, field
from typing import Callable, Optional
import webbrowser
from datetime import datetime
import psutil
import pyautogui
import requests
import skills
import scheduler


@dataclass
class ToolInfo:
    """Schema + metadata for a system tool."""
    name: str
    handler: Callable[[dict], str]
    description: str = ''
    args: dict = field(default_factory=dict)
    critical: bool = False
    examples: list = field(default_factory=list)

    def validate_args(self, args: dict) -> dict:
        cleaned = {}
        for arg_name, (default, arg_type, required) in self.args.items():
            raw = args.get(arg_name)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                cleaned[arg_name] = default if not required else (default or '')
            else:
                try:
                    if arg_type == bool:
                        cleaned[arg_name] = str(raw).lower() in ("true", "1", "yes")
                    elif arg_type == int:
                        cleaned[arg_name] = arg_type(str(raw).strip())
                    else:
                        cleaned[arg_name] = str(raw).strip()
                except (ValueError, TypeError):
                    cleaned[arg_name] = default
        return cleaned


class ToolRegistry:
    """Registry of all system tools with metadata."""
    def __init__(self):
        self._tools: dict[str, ToolInfo] = {}

    def register(self, tool: ToolInfo):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolInfo]:
        return self._tools.get(name)

    def execute(self, name: str, args: dict) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"ERROR: unknown tool '{name}'"
        validated = tool.validate_args(args)
        return tool.handler(validated)

    def is_critical(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool.critical if tool else False

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def count(self) -> int:
        return len(self._tools)
    def generate_tool_list(self, phone_list=None, skill_list=None, sched_list=None):
        result = []
        result.append("You have tools. To use one, output [CALL_TOOL: tool_name, args]")
        result.append("on its own line BEFORE your reply.")
        result.append("")
        result.append("TOOLS:")
        for t in self._tools.values():
            if t.examples:
                for ex in t.examples:
                    result.append(f"  [CALL_TOOL: {t.name}, {ex}]")
            elif t.args:
                ex_parts = []
                for k, v in t.args.items():
                    val = v[0] if v[0] is not None else ""
                    ex_parts.append(f"{k}={val}")
                ex_str = ", ".join(ex_parts)
                result.append(f"  [CALL_TOOL: {t.name}, {ex_str}]  -- {t.description}")
            else:
                result.append(f"  [CALL_TOOL: {t.name}]  -- {t.description}")
        if phone_list:
            result.append(str(phone_list))
        if skill_list:
            result.append(str(skill_list))
        if sched_list:
            result.append(str(sched_list))
        return "\n".join(result)


_registry = ToolRegistry()

def get_registry() -> ToolRegistry:
    return _registry


# ====================================================================
#  TOOL HANDLER IMPLEMENTATIONS
#  (Registered into the ToolRegistry by _register_tools below)
# ====================================================================


def _do_get_weather(city):
    """Weather lookup via Open-Meteo."""
    try:
        r = requests.get('https://geocoding-api.open-meteo.com/v1/search', params={"name":city,"count":1,"language":"en","format":"json"}, timeout=8).json()
        if not r.get("results"): return "ERROR: city not found: " + str(city)
        loc = r["results"][0]
        w = requests.get('https://api.open-meteo.com/v1/forecast', params={
            "latitude": loc["latitude"], "longitude": loc["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "temperature_unit": "celsius", "wind_speed_unit": "kmh", "timezone": "auto"}, timeout=8).json()
        c = w["current"]
        wmo = ["clear sky","mainly clear","partly cloudy","overcast","foggy","drizzle","rain","snow","showers","thunderstorm"]
        wc = c.get("weather_code", 0)
        desc = wmo[0] if wc == 0 else (wmo[1] if wc == 1 else (wmo[2] if wc in (2,) else (wmo[3] if wc == 3 else (wmo[4] if wc == 45 else str(wc)))))
        lines = ["Weather in " + str(loc.get("name", city)) + ": " + desc]
        lines.append("Temperature: " + str(c["temperature_2m"]) + "C (feels " + str(c["apparent_temperature"]) + "C)")
        lines.append("Humidity: " + str(c["relative_humidity_2m"]) + "%  Wind: " + str(c["wind_speed_10m"]) + " km/h")
        return chr(10).join(lines)
    except Exception as e:
        return "ERROR: weather failed: " + str(e)


def _handler_open_browser_url(args: dict) -> str:
    """Open a URL in the default browser. Takes 'url' arg."""
    url = args.get("url", "")
    if url:
        webbrowser.open(url)
    return ""


def _handler_get_current_time(args: dict) -> str:
    """Return the current date and time as a formatted string. No args needed."""
    now = datetime.now()
    return now.strftime("%A, %B %d, %Y  %I:%M %p")


def _handler_check_system_vitals(args: dict) -> str:
    """Check CPU, RAM, and disk usage. No args needed."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return (
        f"CPU: {cpu}%\n"
        f"RAM: {mem.percent}% ({mem.used // (1024**3)}GB / {mem.total // (1024**3)}GB)\n"
        f"Disk: {disk.percent}% ({disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB)"
    )


def _handler_control_system_media(args: dict) -> str:
    """Control media playback. Takes 'command' arg: mute, volume_up, volume_down, play_pause, next, previous."""
    cmd = args.get("command", "")
    key_map = {
        "mute": "volumemute", "volume_up": "volumeup", "volume_down": "volumedown",
        "play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack",
    }
    key = key_map.get(cmd)
    if key:
        pyautogui.press(key)
        return f"media: {cmd}"
    return f"unknown media command: {cmd}"


def _handler_get_weather(args: dict) -> str:
    """Get current weather for a city. Takes optional 'city' arg (default: Proddatur)."""
    city = args.get("city", "Proddatur")
    return _do_get_weather(city)


def _handler_read_notifications(args: dict) -> str:
    """Read recent Windows toast notifications. No args needed."""
    from jarvis_engine import read_windows_notifications
    return read_windows_notifications()


def _handler_delete_files(args: dict) -> str:
    """Move files from a folder to Jarvis trash (recoverable). Takes 'folder_name' and 'file_filter' args. CRITICAL tool."""
    from jarvis_engine import delete_files_in_folder, USER_HOME
    folder = args.get("path") or args.get("folder_name", "downloads")
    return delete_files_in_folder(
        folder,
        args.get("file_filter", "all"),
        USER_HOME
    )


def _handler_undo_last_action(args: dict) -> str:
    """Undo the last file operation (delete or sort). No args needed."""
    from jarvis_engine import undo_last_action
    return undo_last_action()


def _handler_run_pending_script(args: dict) -> str:
    """Run a pending generated script. No args needed. CRITICAL tool."""
    return "no pending script to run"


def _handler_recall(args: dict) -> str:
    """Recall saved facts about the user. Takes optional 'key' arg; empty key lists all."""
    from jarvis_engine import db_recall_all, db_recall
    key = args.get("key", "")
    if not key:
        all_mem = db_recall_all()
        things = [(k, v) for k, v in all_mem if not k.startswith("_")]
        if not things:
            return "i don't know anything yet babe 🥺"
        lines = [f"  {k}: {v}" for k, v in things]
        return "here's what i know:\n" + "\n".join(lines)
    val = db_recall(key)
    if val:
        return f"{key}: {val}"
    return f"i don't know ur {key} yet babe 🥺"


def _handler_search_memory(args: dict) -> str:
    """Full-text search through saved memories. Takes 'query' arg."""
    from jarvis_engine import db_search_memory
    query = args.get("query", "")
    if not query:
        return "what should i search for babe??"
    results = db_search_memory(query)
    if not results:
        return f"nothing found for '{query}' in my memories babe 😭"
    lines = [f"  {k}: {v}" for k, v, _ in results]
    return f"found {len(results)} memory/ies for '{query}':\n" + "\n".join(lines)


def _handler_list_contacts(args: dict) -> str:
    """List all saved contacts. No args needed."""
    from jarvis_engine import db_list_contacts
    contacts = db_list_contacts()
    if not contacts:
        return "no saved contacts yet babe"
    lines = [f"  {name}: {number} ({platform})" for name, number, platform in contacts]
    return "contacts:\n" + "\n".join(lines)


def _handler_get_mood(args: dict) -> str:
    """Get current mood level, mode, and active mood name. No args needed."""
    from jarvis_engine import get_mood_level, get_mode, get_current_mood, MOOD_NAMES
    level = get_mood_level()
    mood_mode = get_mode()
    current_mood = get_current_mood()
    return f"Mood level: {level} ({MOOD_NAMES[level]})  Mode: {mood_mode}  Current: {current_mood}"


def _handler_list_skills(args: dict) -> str:
    """List all learned skills for the active profile. No args needed."""
    all_skills = skills.list_skills()
    if not all_skills:
        return "no skills learned yet babe"
    lines = []
    for s in all_skills:
        lines.append(f"  {s['name']} ({s['usage_count']}x) — {s['description']}")
        phrases = ", ".join(s["trigger_phrases"][:3])
        if phrases:
            lines.append(f"    triggers: {phrases}")
    return "learned skills:\n" + "\n".join(lines)


def _handler_delete_skill(args: dict) -> str:
    """Delete a learned skill by name. Takes 'name' arg."""
    name = args.get("name", "")
    if not name:
        return "which skill to delete??"
    ok = skills.delete_skill(name)
    return f"forgot skill '{name}'!!" if ok else f"skill '{name}' not found babe"


def _handler_list_tasks(args: dict) -> str:
    """List all scheduled tasks for the active profile. No args needed."""
    all_tasks = scheduler.list_tasks()
    if not all_tasks:
        return "no scheduled tasks babe"
    lines = []
    for t in all_tasks:
        status = "✅" if t["enabled"] else "⏸"
        lines.append(f"  {status} {t['name']} — {t['schedule_desc']}")
        if t["last_run"]:
            lines.append(f"     last run: {t['last_run']}")
    return "scheduled tasks:\n" + "\n".join(lines)


def _handler_delete_task(args: dict) -> str:
    """Delete a scheduled task by name. Takes 'name' arg."""
    name = args.get("name", "")
    if not name:
        return "which task to delete??"
    ok = scheduler.delete_task(name)
    return f"deleted task '{name}'!!" if ok else f"task '{name}' not found babe"


def _handler_toggle_task(args: dict) -> str:
    """Enable or disable a scheduled task. Takes 'name' and optional 'enabled' args."""
    name = args.get("name", "")
    if not name:
        return "which task to toggle??"
    enabled_str = args.get("enabled", "")
    if enabled_str.lower() in ("true", "1", "yes"):
        enabled = True
    elif enabled_str.lower() in ("false", "0", "no"):
        enabled = False
    else:
        enabled = None  # toggle
    ok, msg = scheduler.toggle_task(name, enabled)
    return msg


def _handler_web_search(args: dict) -> str:
    """Search the internet using DuckDuckGo. Takes 'query' arg."""
    query = args.get("query", "").strip()
    if not query:
        return "what should i search for babe??"
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return f"no results found for '{query}' babe 😭"
        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "").strip()
            snippet = r.get("body", "").strip() or r.get("snippet", "").strip()
            link = r.get("href", "").strip()
            lines.append(f"{i}. {title}")
            if snippet:
                lines.append(f"   {snippet[:200]}")
            if link:
                lines.append(f"   {link}")
        return f"web search results for '{query}':\n" + "\n".join(lines)
    except ImportError:
        return "ERROR: duckduckgo_search not installed. Run: pip install duckduckgo_search"
    except Exception as e:
        # fallback: try DuckDuckGo lite API directly via requests
        try:
            import requests
            url = "https://lite.duckduckgo.com/lite/"
            r = requests.post(url, data={"q": query}, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code != 200:
                return f"search failed: {str(e)}"
            from html.parser import HTMLParser
            class DDGTextParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.results = []
                    self._in_result = False
                    self._current = {}
                    self._tag = ""
                def handle_starttag(self, tag, attrs):
                    self._tag = tag
                    attrs_dict = dict(attrs)
                    if tag == "a" and attrs_dict.get("class") == "result-link":
                        self._in_result = True
                        self._current["href"] = attrs_dict.get("href", "")
                def handle_data(self, data):
                    if self._in_result:
                        self._current.setdefault("text", "")
                        self._current["text"] += data
                def handle_endtag(self, tag):
                    if tag == "a" and self._in_result and self._current.get("text", "").strip():
                        self.results.append(self._current)
                        self._current = {}
                        self._in_result = False
            parser = DDGTextParser()
            parser.feed(r.text)
            links = parser.results[:5]
            if not links:
                return f"no results for '{query}' babe"
            formatted = [f"{i}. {l.get('text','').strip()[:150]}\n   {l.get('href','')}" for i, l in enumerate(links, 1)]
            return f"web search results for '{query}':\n" + "\n".join(formatted)
        except Exception as e2:
            return f"search failed: {str(e2)}"


def _handler_send_social_message(args: dict) -> str:
    """Send a social message. Handled by pre-router for complex arg parsing."""
    from jarvis_engine import send_social_message
    platform = args.get("platform", "")
    contact = args.get("contact_name", "")
    message = args.get("message", "")
    if platform and contact and message:
        return send_social_message(platform, contact, message)
    return "i handle messaging through chat — try 'send [message] in [group] on insta' or 'send [message] to [name] on whatsapp' 💕"


def _handler_network_status(args: dict) -> str:
    """Get network adapter info, IP, WiFi/Ethernet status."""
    import subprocess
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object Name,InterfaceDescription,LinkSpeed | Format-List"],
            capture_output=True, text=True, timeout=8
        )
        adapters = result.stdout.strip()

        ip_result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '169.*' -and $_.IPAddress -ne '127.0.0.1'} | Select-Object InterfaceAlias,IPAddress | Format-List"],
            capture_output=True, text=True, timeout=8
        )
        ips = ip_result.stdout.strip()

        wifi_result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=8
        )
        wifi_lines = [l.strip() for l in wifi_result.stdout.splitlines() if any(k in l for k in ["SSID", "Signal", "State", "Radio type"])]
        wifi_info = "\n".join(wifi_lines) if wifi_lines else "Not connected to WiFi"

        return f"Active Adapters:\n{adapters}\n\nIP Addresses:\n{ips}\n\nWiFi:\n{wifi_info}"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_list_processes(args: dict) -> str:
    """List running processes, optionally filtered by name."""
    import psutil
    filter_name = args.get("filter", "").lower().strip()
    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                name = p.info["name"] or ""
                if filter_name and filter_name not in name.lower():
                    continue
                mem_mb = round(p.info["memory_info"].rss / (1024 * 1024), 1) if p.info["memory_info"] else 0
                procs.append((name, p.info["pid"], mem_mb))
            except Exception:
                continue
        if not procs:
            return f"no processes matching '{filter_name}'" if filter_name else "no processes found"
        procs.sort(key=lambda x: x[2], reverse=True)
        lines = [f"  {name} (PID {pid}) — {mem}MB" for name, pid, mem in procs[:20]]
        return f"Top processes:\n" + "\n".join(lines)
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_kill_process(args: dict) -> str:
    """Kill a process by name or PID."""
    import psutil
    name = args.get("name", "").strip()
    pid = args.get("pid", "").strip()
    try:
        killed = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if pid and str(p.info["pid"]) == pid:
                    p.kill()
                    killed.append(f"{p.info['name']} (PID {pid})")
                elif name and name.lower() in (p.info["name"] or "").lower():
                    p.kill()
                    killed.append(f"{p.info['name']} (PID {p.info['pid']})")
            except Exception:
                continue
        if killed:
            return f"killed: {', '.join(killed)}"
        return f"no process found matching '{name or pid}'"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_run_command(args: dict) -> str:
    """Run a shell command and return output."""
    import subprocess
    cmd = args.get("command", "").strip()
    if not cmd:
        return "no command provided"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if out:
            return out[:1500]
        if err:
            return f"stderr: {err[:1500]}"
        return "command ran (no output)"
    except subprocess.TimeoutExpired:
        return "command timed out after 15s"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_get_installed_apps(args: dict) -> str:
    """List installed applications."""
    import subprocess
    filter_name = args.get("filter", "").lower().strip()
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*, "
             "HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* "
             "| Select-Object DisplayName, DisplayVersion "
             "| Where-Object {$_.DisplayName -ne $null} "
             "| Sort-Object DisplayName"],
            capture_output=True, text=True, timeout=15
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and "---" not in l and "DisplayName" not in l]
        if filter_name:
            lines = [l for l in lines if filter_name in l.lower()]
        if not lines:
            return f"no apps found matching '{filter_name}'" if filter_name else "no apps found"
        return "Installed apps:\n" + "\n".join(lines[:50])
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_clipboard_read(args: dict) -> str:
    """Read current clipboard contents."""
    try:
        import pyperclip
        content = pyperclip.paste()
        if not content or not content.strip():
            return "clipboard is empty"
        return f"clipboard: {content[:1000]}"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_clipboard_write(args: dict) -> str:
    """Write text to clipboard."""
    text = args.get("text", "").strip()
    if not text:
        return "nothing to write to clipboard"
    try:
        import pyperclip
        pyperclip.copy(text)
        return f"copied to clipboard!!"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_type_text(args: dict) -> str:
    """Type text as keyboard input."""
    text = args.get("text", "").strip()
    if not text:
        return "no text to type"
    try:
        import pyautogui, pyperclip, time
        pyperclip.copy(text)
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "v")
        return f"typed: {text[:100]}"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_press_key(args: dict) -> str:
    """Press a keyboard key or combo like ctrl+c, alt+tab, enter, etc."""
    key = args.get("key", "").strip()
    if not key:
        return "no key provided"
    try:
        import pyautogui
        if "+" in key:
            parts = [k.strip() for k in key.split("+")]
            pyautogui.hotkey(*parts)
        else:
            pyautogui.press(key)
        return f"pressed: {key}"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_disk_info(args: dict) -> str:
    """Get disk usage for all drives."""
    import psutil
    try:
        lines = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                total_gb = round(usage.total / (1024**3), 1)
                used_gb = round(usage.used / (1024**3), 1)
                free_gb = round(usage.free / (1024**3), 1)
                lines.append(f"  {part.device} ({part.fstype}) — {used_gb}GB used / {total_gb}GB total ({usage.percent}%) — {free_gb}GB free")
            except Exception:
                continue
        return "Drives:\n" + "\n".join(lines) if lines else "no drives found"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_shutdown(args: dict) -> str:
    """Shutdown, restart, or sleep the PC."""
    import subprocess
    action = args.get("action", "shutdown").lower().strip()
    delay = args.get("delay", "10").strip()
    try:
        delay_int = int(delay)
    except Exception:
        delay_int = 10
    cmds = {
        "shutdown": ["shutdown", "/s", "/t", str(delay_int)],
        "restart":  ["shutdown", "/r", "/t", str(delay_int)],
        "sleep":    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "cancel":   ["shutdown", "/a"],
    }
    cmd = cmds.get(action)
    if not cmd:
        return f"unknown action '{action}' — use shutdown/restart/sleep/cancel"
    try:
        subprocess.run(cmd, check=True)
        return f"{action} scheduled in {delay_int}s — say 'cancel shutdown' to abort" if action not in ("sleep", "cancel") else f"{action} done"
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_open_settings(args: dict) -> str:
    """Open a Windows Settings page."""
    import subprocess
    page = args.get("page", "").strip().lower()
    pages = {
        "network":      "ms-settings:network",
        "wifi":         "ms-settings:network-wifi",
        "bluetooth":    "ms-settings:bluetooth",
        "display":      "ms-settings:display",
        "sound":        "ms-settings:sound",
        "apps":         "ms-settings:appsfeatures",
        "startup":      "ms-settings:startupapps",
        "power":        "ms-settings:powersleep",
        "storage":      "ms-settings:storagesense",
        "update":       "ms-settings:windowsupdate",
        "privacy":      "ms-settings:privacy",
        "taskmanager":  "taskmgr",
        "":             "ms-settings:",
    }
    uri = pages.get(page, f"ms-settings:{page}")
    try:
        subprocess.Popen(["start", uri], shell=True)
        return f"opened settings: {page or 'home'}"
    except Exception as e:
        return f"ERROR: {str(e)}"



# ── PW Schedule ──────────────────────────────────────────────────────────────

def _handler_get_class_schedule(args: dict) -> str:
    """Fetch PW class schedule. 'date' arg = YYYY-MM-DD, 'range' arg = today/tomorrow/week."""
    try:
        from pw_schedule import (
            get_todays_schedule_text,
            get_tomorrows_schedule_text,
            get_weekly_schedule_text,
            get_schedule_text_for_date,
        )
        date  = args.get("date", "").strip()
        range_ = args.get("range", "").strip().lower()
        if range_ == "week":
            return get_weekly_schedule_text()
        if range_ == "tomorrow":
            return get_tomorrows_schedule_text()
        if date:
            return get_schedule_text_for_date(date)
        return get_todays_schedule_text()
    except ImportError:
        return "ERROR: pw_schedule.py not found babe — make sure it's in ur Jarvis folder!!"
    except Exception as e:
        return f"ERROR fetching schedule: {str(e)[:200]}"


def _handler_set_pw_token(args: dict) -> str:
    """Save the PW auth token."""
    token = args.get("token", "").strip()
    if not token:
        return "give me the token babe!! say: set my pw token to Bearer eyJ..."
    try:
        from pw_schedule import set_pw_token
        return set_pw_token(token)
    except Exception as e:
        return f"ERROR: {str(e)}"


def _handler_set_pw_batch_id(args: dict) -> str:
    """Save the PW batch ID."""
    batch_id = args.get("batch_id", "").strip()
    if not batch_id:
        return "give me the batch ID babe!!"
    try:
        from pw_schedule import set_pw_batch_id
        return set_pw_batch_id(batch_id)
    except Exception as e:
        return f"ERROR: {str(e)}"



def _register_tools():
    """Register all system tool handlers in the ToolRegistry."""
    reg = get_registry()

    # ── Browser ──
    reg.register(ToolInfo(
        name="open_browser_url",
        handler=_handler_open_browser_url,
        description="Open a URL in the default browser",
        args={"url": ("", str, True)},
        examples=["url=https://youtube.com", "url=https://google.com"],
    ))

    # ── Time ──
    reg.register(ToolInfo(
        name="get_current_time",
        handler=_handler_get_current_time,
        description="Get the current date and time",
    ))

    # ── System ──
    reg.register(ToolInfo(
        name="check_system_vitals",
        handler=_handler_check_system_vitals,
        description="Check CPU, RAM, and disk usage",
    ))
    reg.register(ToolInfo(
        name="control_system_media",
        handler=_handler_control_system_media,
        description="Control system media playback (mute, volume, play/pause, next/previous)",
        args={"command": ("play_pause", str, True)},
        examples=["command=play_pause", "command=volume_up", "command=mute"],
    ))

    # ── Weather ──
    reg.register(ToolInfo(
        name="get_weather",
        handler=_handler_get_weather,
        description="Get current weather for a city",
        args={"city": ("Proddatur", str, False)},
        examples=["city=Tokyo", "city=London"],
    ))

    # ── Notifications ──
    reg.register(ToolInfo(
        name="read_notifications",
        handler=_handler_read_notifications,
        description="Read recent Windows toast notifications",
    ))

    # ── Files ──
    reg.register(ToolInfo(
        name="delete_files",
        handler=_handler_delete_files,
        description="Move files from a folder to the Jarvis trash (recoverable). Use folder_name for known folders/subfolders or path for a full folder path.",
        args={"folder_name": ("downloads", str, False), "file_filter": ("all", str, False), "path": (None, str, False)},
        critical=True,
        examples=[
            "folder_name=downloads, file_filter=videos",
            "folder_name=random_installers, file_filter=all",
            "path=C:\\Users\\me\\Downloads\\random_installers, file_filter=all",
        ],
    ))
    reg.register(ToolInfo(
        name="undo_last_action",
        handler=_handler_undo_last_action,
        description="Undo the last file operation (delete or sort)",
    ))
    reg.register(ToolInfo(
        name="run_pending_script",
        handler=_handler_run_pending_script,
        description="Run a pending generated script",
        critical=True,
    ))

    # ── Memory ──
    reg.register(ToolInfo(
        name="recall",
        handler=_handler_recall,
        description="Recall saved facts about the user",
        args={"key": ("", str, False)},
        examples=["key=name", "key=favorite_color"],
    ))
    reg.register(ToolInfo(
        name="search_memory",
        handler=_handler_search_memory,
        description="Full-text search through saved memories",
        args={"query": ("", str, True)},
        examples=["query=favorite food", "query=birthday"],
    ))

    # ── Contacts ──
    reg.register(ToolInfo(
        name="list_contacts",
        handler=_handler_list_contacts,
        description="List saved contacts",
    ))

    # ── Mood ──
    reg.register(ToolInfo(
        name="get_mood",
        handler=_handler_get_mood,
        description="Get current mood level and mode",
    ))

    # ── Skills ──
    reg.register(ToolInfo(
        name="list_skills",
        handler=_handler_list_skills,
        description="List all learned skills",
    ))
    reg.register(ToolInfo(
        name="delete_skill",
        handler=_handler_delete_skill,
        description="Delete a learned skill by name",
        args={"name": ("", str, True)},
        examples=["name=open_youtube"],
    ))

    # ── Scheduler ──
    reg.register(ToolInfo(
        name="list_tasks",
        handler=_handler_list_tasks,
        description="List all scheduled tasks",
    ))
    reg.register(ToolInfo(
        name="delete_task",
        handler=_handler_delete_task,
        description="Delete a scheduled task by name",
        args={"name": ("", str, True)},
        examples=["name=check_weather"],
    ))
    reg.register(ToolInfo(
        name="toggle_task",
        handler=_handler_toggle_task,
        description="Enable or disable a scheduled task",
        args={"name": ("", str, True), "enabled": ("true", str, False)},
        examples=["name=check_weather, enabled=false"],
    ))

    # ── Web Search ──
    reg.register(ToolInfo(
        name="web_search",
        handler=_handler_web_search,
        description="Search the internet for current information",
        args={"query": ("", str, True)},
        examples=["query=latest AI news 2026", "query=weather in Tokyo"],
    ))


    # ── PW Class Schedule ──
    reg.register(ToolInfo(
        name="get_class_schedule",
        handler=_handler_get_class_schedule,
        description="Fetch today's Physics Wallah (PW) class schedule — say 'my classes today' or 'schedule for tomorrow'",
        args={"date": ("", str, False)},
        examples=["date=", "date=2026-06-25", "range=tomorrow", "range=week"],
    ))
    reg.register(ToolInfo(
        name="set_pw_token",
        handler=_handler_set_pw_token,
        description="Save PW auth token (get from browser DevTools → Authorization header)",
        args={"token": ("", str, True)},
        examples=["token=Bearer eyJhbGciOiJSUzI1..."],
    ))
    reg.register(ToolInfo(
        name="set_pw_batch_id",
        handler=_handler_set_pw_batch_id,
        description="Save PW batch ID",
        args={"batch_id": ("", str, True)},
        examples=["batch_id=698ad3519549b300a5e1cc6a"],
    ))

    # ── Messaging (critical, handled by pre-router but registered for gate) ──
    reg.register(ToolInfo(
        name="send_social_message",
        handler=_handler_send_social_message,
        description="Send a message on WhatsApp or Instagram",
        args={"platform": ("whatsapp", str, False), "contact_name": ("", str, False), "message": ("", str, False)},
        critical=True,
        examples=[
            "platform=whatsapp, contact_name=Mom, message=hey!!",
            "platform=instagram, contact_name=diamond chat group, message=hi",
        ],
    ))

    # ── Network ──
    reg.register(ToolInfo(
        name="network_status",
        handler=_handler_network_status,
        description="Get network adapter info, IP address, WiFi/Ethernet status and signal",
        examples=["[CALL_TOOL: network_status]"],
    ))

    # ── Processes ──
    reg.register(ToolInfo(
        name="list_processes",
        handler=_handler_list_processes,
        description="List running processes, optionally filter by name",
        args={"filter": ("", str, False)},
        examples=["filter=chrome", "filter="],
    ))
    reg.register(ToolInfo(
        name="kill_process",
        handler=_handler_kill_process,
        description="Kill a running process by name or PID",
        args={"name": ("", str, False), "pid": ("", str, False)},
        examples=["name=chrome", "pid=1234"],
        critical=True,
    ))

    # ── Shell ──
    reg.register(ToolInfo(
        name="run_command",
        handler=_handler_run_command,
        description="Run any shell/cmd command and return output",
        args={"command": ("", str, True)},
        examples=["command=ipconfig", "command=dir C:\\Users", "command=tasklist"],
        critical=True,
    ))

    # ── Apps ──
    reg.register(ToolInfo(
        name="get_installed_apps",
        handler=_handler_get_installed_apps,
        description="List installed applications, optionally filter by name",
        args={"filter": ("", str, False)},
        examples=["filter=adobe", "filter="],
    ))

    # ── Clipboard ──
    reg.register(ToolInfo(
        name="clipboard_read",
        handler=_handler_clipboard_read,
        description="Read current clipboard contents",
    ))
    reg.register(ToolInfo(
        name="clipboard_write",
        handler=_handler_clipboard_write,
        description="Write text to clipboard",
        args={"text": ("", str, True)},
        examples=["text=hello world"],
    ))

    # ── Keyboard ──
    reg.register(ToolInfo(
        name="type_text",
        handler=_handler_type_text,
        description="Type text as keyboard input into the active window",
        args={"text": ("", str, True)},
        examples=["text=hello babe"],
    ))
    reg.register(ToolInfo(
        name="press_key",
        handler=_handler_press_key,
        description="Press a key or key combo like ctrl+c, alt+tab, enter, escape",
        args={"key": ("", str, True)},
        examples=["key=enter", "key=ctrl+c", "key=alt+tab", "key=escape"],
    ))

    # ── Disk ──
    reg.register(ToolInfo(
        name="disk_info",
        handler=_handler_disk_info,
        description="Get disk usage for all drives (C, D, E, etc.)",
    ))

    # ── Power ──
    reg.register(ToolInfo(
        name="shutdown_pc",
        handler=_handler_shutdown,
        description="Shutdown, restart, sleep or cancel shutdown of the PC",
        args={"action": ("shutdown", str, True), "delay": ("10", str, False)},
        examples=["action=shutdown, delay=30", "action=restart, delay=10", "action=sleep", "action=cancel"],
        critical=True,
    ))

    # ── Settings ──
    reg.register(ToolInfo(
        name="open_settings",
        handler=_handler_open_settings,
        description="Open Windows Settings pages: network, wifi, bluetooth, display, sound, apps, startup, power, storage, update, privacy, taskmanager",
        args={"page": ("", str, False)},
        examples=["page=network", "page=display", "page=taskmanager", "page=wifi"],
    ))

    count = reg.count()
    print(f"[TOOLS] registered {count} tools ✅")


_register_tools()