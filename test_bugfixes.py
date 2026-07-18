"""
JARVIS BUGFIX VERIFICATION TESTS
Run: python test_bugfixes.py
"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

# Set env so jarvis_engine.py doesn't sys.exit(1) on import
# Force-override to ensure our test values are used
os.environ["JARVIS_API_KEY"] = "test-skip-key"
os.environ["JARVIS_PASSWORD"] = "testpassword123"

PASS = 0
FAIL = 0

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} - {detail}")


print("═" * 50)
print("  JARVIS BUGFIX VERIFICATION TESTS")
print("═" * 50)

# ── Import jarvis_engine to trigger tool registration ──────────
print("\n--- Importing jarvis_engine (triggers tool registration) ---")
try:
    import jarvis_engine
    test("jarvis_engine imports OK", True)
except Exception as e:
    test("jarvis_engine imports OK", False, str(e))

# ── Test 1: Tool Registry ──────────────────────────────────────
print("\n--- Test 1: Tool Registry ---")
from jarvis_tools import get_registry

reg = get_registry()
tool_names = [
    "open_browser_url", "get_current_time", "check_system_vitals",
    "control_system_media", "get_weather", "read_notifications",
    "delete_files", "undo_last_action", "run_pending_script",
    "recall", "search_memory", "list_contacts", "get_mood",
    "list_skills", "delete_skill", "list_tasks", "delete_task",
    "toggle_task",
]
for name in tool_names:
    test(f"Tool registered: {name}", reg.has_tool(name), f"'{name}' not found in registry")

tool_count = reg.count()
test(f"Registry has {tool_count} tools", tool_count > 0, f"Only {tool_count} tools")

# ── Test 2: Tool Argument Validation ───────────────────────────
print("\n--- Test 2: Tool Argument Validation ---")
tool = reg.get("get_weather")
if tool:
    validated = tool.validate_args({"city": "Tokyo"})
    test("Weather city arg passes", validated.get("city") == "Tokyo")
else:
    test("Weather tool exists in registry", False, "get_weather not found")

tool = reg.get("delete_files")
if tool:
    validated = tool.validate_args({"folder_name": "downloads", "file_filter": "videos"})
    test("Delete files args pass", validated.get("folder_name") == "downloads" and validated.get("file_filter") == "videos")

    validated = tool.validate_args({})
    test("Delete files empty args get defaults", validated.get("folder_name") == "downloads" and validated.get("file_filter") == "all")
else:
    test("Delete files tool exists in registry", False, "delete_files not found")

# ── Test 3: Tool Critical Flags ────────────────────────────────
print("\n--- Test 3: Tool Critical Flags ---")
test("delete_files is critical", reg.is_critical("delete_files"))
test("run_pending_script is critical", reg.is_critical("run_pending_script"))
test("get_current_time is NOT critical", not reg.is_critical("get_current_time"))
test("get_weather is NOT critical", not reg.is_critical("get_weather"))

# ── Test 4: Module Imports ─────────────────────────────────────
print("\n--- Test 4: Module Imports ---")
modules = [
    ("profiles", "profiles"),
    ("password_gate", "password_gate"),
    ("skills", "skills"),
    ("scheduler", "scheduler"),
    ("jarvis_tools", "jarvis_tools"),
    ("jarvis_engine", "jarvis_engine"),
]
for name, path in modules:
    try:
        __import__(path)
        test(f"Import {name}", True)
    except Exception as e:
        test(f"Import {name}", False, str(e))

# ── Test 5: Password Gate ──────────────────────────────────────
print("\n--- Test 5: Password Gate ---")
from password_gate import (
    is_unlocked, is_locked_out,
    reset_session, MAX_ATTEMPTS,
    is_critical_tool, is_critical_message,
)

reset_session()
test("Gate starts locked", not is_unlocked())
test("Gate starts not locked out", not is_locked_out())
test("MAX_ATTEMPTS is reasonable", MAX_ATTEMPTS >= 1 and MAX_ATTEMPTS <= 5)
test("delete_files is critical tool", is_critical_tool("delete_files"))
test("critical message detected", is_critical_message("delete my files"))
test("non-critical message ignored", not is_critical_message("hello how are you"))

# Unlock test
from password_gate import attempt_unlock
ok = attempt_unlock("testpassword123")
test("Correct password unlocks gate", ok)
test("Gate is now unlocked", is_unlocked())

reset_session()

# ── Test 6: Profile System ─────────────────────────────────────
print("\n--- Test 6: Profile System ---")
import importlib
import profiles
importlib.reload(profiles)

profiles.reset_session()
test("Default profile is cherry", profiles.get_active_profile() == "cherry")
test("Cherry active check", profiles.is_cherry_active())
test("Cherry not unlocked", not profiles.is_cherry_unlocked())
test("Non-restricted tool allowed", profiles.is_tool_allowed("get_current_time"))
test("Restricted tool locked", not profiles.is_tool_allowed("delete_files"))

# ── Test 7: Schedule Parser ────────────────────────────────────
print("\n--- Test 7: Schedule Parser ---")
from scheduler import parse_schedule

test_cases = [
    ("every 30 minutes", "interval"),
    ("in 5 minutes", "one_shot"),
    ("in 2 hours", "one_shot"),
]
for text, expected_type in test_cases:
    try:
        s_type, s_val = parse_schedule(text)
        test(f"Schedule parse: '{text}' → {s_type}", s_type == expected_type)
    except ValueError as e:
        test(f"Schedule parse: '{text}'", False, str(e))

# ── Test 8: Database Functions ─────────────────────────────────
print("\n--- Test 8: DB Functions ---")
from jarvis_engine import (
    db_remember, db_recall, db_recall_all,
    db_save_contact, db_get_contact, db_list_contacts,
)

# Test memory
db_remember("_test_key", "test_value")
test("db_remember + db_recall", db_recall("_test_key") == "test_value")

# Test contacts
db_save_contact("test_user", "+1234567890")
contact = db_get_contact("test_user")
test("db_save_contact + db_get_contact", contact is not None and contact[0] == "+1234567890")
contacts = db_list_contacts()
test("db_list_contacts works", any("test_user" in str(c) for c in contacts))

# ── Test 9: _mods.py fixed ─────────────────────────────────────
print("\n--- Test 9: _mods.py fixed ---")
with open("_mods.py") as f:
    content = f.read()
test("_mods.py is valid Python file", os.path.getsize("_mods.py") > 50 and "✅" in content)
test("_mods.py not truncated", "already" in content.lower())

# ── Summary ─────────────────────────────────────────────────────
print("\n" + "═" * 50)
print(f"  RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
print("═" * 50)

if FAIL > 0:
    print("\n  ❌ Some tests FAILED — check details above!")
else:
    print("\n  ✅ All tests passed!!")

sys.exit(0 if FAIL == 0 else 1)
