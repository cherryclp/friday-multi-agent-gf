"""
Comprehensive test suite for the Jarvis Profile System.
Tests profiles.py, password_gate.py integration, and jarvis_engine.py profile-aware behavior.
"""
import sys
import os
import sqlite3

sys.stdout.reconfigure(encoding="utf-8")

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

# ─── Setup: clean test DB ─────────────────────────────────────
JARVIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(JARVIS_DIR, "jarvis_memory.db")

# Remove the profiles table so we start fresh
import profiles
try:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DROP TABLE IF EXISTS profiles")
        print("Dropped existing profiles table for clean test")
except Exception as e:
    print(f"Note: could not drop table: {e}")

# Force re-import profiles module to trigger fresh _init_db
import importlib
importlib.reload(profiles)

print("\n═══════════════════════════════════════════")
print("  PROFILE SYSTEM TESTS")
print("═══════════════════════════════════════════")

# ─── Test 1: Cherry profile auto-created ─────────────────────
print("\n--- Test 1: Cherry auto-creation ---")
profiles_list = profiles.list_profiles()
test("Cherry exists on first boot", any(p["name"] == "cherry" for p in profiles_list),
     f"Profiles found: {[p['name'] for p in profiles_list]}")

cherry = next(p for p in profiles_list if p["name"] == "cherry")
test("Cherry is marked as cherry", cherry["is_cherry"])

# ─── Test 2: Default active profile ──────────────────────────
print("\n--- Test 2: Active profile defaults ---")
test("Default active is cherry", profiles.get_active_profile() == "cherry")
test("is_cherry_active() returns True", profiles.is_cherry_active())
test("Cherry not unlocked yet", not profiles.is_cherry_unlocked())
test("cherry_can_use_critical_tools() is False", not profiles.cherry_can_use_critical_tools())

# ─── Test 3: Profile creation ────────────────────────────────
print("\n--- Test 3: Profile creation ---")
ok, msg = profiles.create_profile("Alex")
test("Create 'Alex' profile succeeds", ok, msg)

ok, msg = profiles.create_profile("Alex")  # Duplicate
test("Create duplicate 'Alex' fails", not ok)

test("list_profiles includes Alex", 
     any(p["name"] == "Alex" for p in profiles.list_profiles()))

# Create with password
ok, msg = profiles.create_profile("Sarah", password="hello123")
test("Create 'Sarah' with password", ok)

test("list_profiles includes Sarah",
     any(p["name"] == "Sarah" for p in profiles.list_profiles()))

test("Active profile still cherry", profiles.get_active_profile() == "cherry")

# ─── Test 4: Profile switching ───────────────────────────────
print("\n--- Test 4: Profile switching ---")
ok = profiles.set_active_profile("Alex")
test("Switch to Alex succeeds", ok)
test("Active profile is now Alex", profiles.get_active_profile() == "Alex")
test("is_cherry_active() is False", not profiles.is_cherry_active())

# Non-Cherry can't use critical tools
test("Alex: delete_files NOT allowed", not profiles.is_tool_allowed("delete_files"))
test("Alex: get_current_time IS allowed", profiles.is_tool_allowed("get_current_time"))
test("Alex: restricted tools set is full", len(profiles.get_restricted_tools()) == len(profiles.RESTRICTED_TOOLS))

# ─── Test 5: Cherry password verification ────────────────────
print("\n--- Test 5: Cherry password verification ---")
profiles.set_active_profile("cherry")
test("Switch back to Cherry", profiles.get_active_profile() == "cherry")

# Wrong password
ok = profiles.verify_profile_password("cherry", "wrongpass")
test("Wrong Cherry password rejected", not ok)
test("Cherry still not unlocked", not profiles.is_cherry_unlocked())

# Correct password (from JARVIS_PASSWORD env or default)
import os
correct_pw = os.environ.get("JARVIS_PASSWORD") or "jarvis123"
ok = profiles.verify_profile_password("cherry", correct_pw)
test("Correct Cherry password accepted", ok)
test("Cherry now unlocked", profiles.is_cherry_unlocked())
test("cherry_can_use_critical_tools() is True", profiles.cherry_can_use_critical_tools())

# After unlock, critical tools are allowed
test("Cherry unlocked: delete_files allowed", profiles.is_tool_allowed("delete_files"))
test("Cherry unlocked: restricted tools empty", len(profiles.get_restricted_tools()) == 0)

# ─── Test 6: Profile system prompts ──────────────────────────
print("\n--- Test 6: Profile system prompts ---")
prompt = profiles.get_profile_system_prompt()
test("Cherry unlocked gets GF prompt", 
     "anime girlfriend" in prompt or "girlfriend" in prompt,
     f"Prompt starts with: {prompt[:60]}...")

# Switch to Alex, check prompt
profiles.set_active_profile("Alex")
prompt = profiles.get_profile_system_prompt()
test("Alex gets neutral prompt containing his name", 
     "Alex" in prompt and "profile" in prompt,
     f"Prompt: {prompt[:80]}...")

# ─── Test 7: Profile deletion ────────────────────────────────
print("\n--- Test 7: Profile deletion ---")
ok, msg = profiles.delete_profile("cherry")
test("Can't delete Cherry profile", not ok, msg)

profiles.set_active_profile("cherry")

ok, msg = profiles.delete_profile("Sarah")
test("Delete 'Sarah' succeeds", ok, msg)
test("Sarah removed from list", 
     not any(p["name"] == "Sarah" for p in profiles.list_profiles()))

ok, msg = profiles.delete_profile("Sarah")
test("Delete non-existent fails", not ok)

# ─── Test 8: Profile info ────────────────────────────────────
print("\n--- Test 8: Profile info ---")
info = profiles.get_profile_info()
test("Profile info has name", info["name"] == "cherry")
test("Profile info has is_cherry", info["is_cherry"])
test("Profile info has cherry_unlocked", info["cherry_unlocked"])

# ─── Test 9: Reset session ───────────────────────────────────
print("\n--- Test 9: Session reset ---")
profiles.set_active_profile("Alex")
profiles.reset_session()
test("Reset goes back to Cherry", profiles.get_active_profile() == "cherry")
test("Reset locks Cherry", not profiles.is_cherry_unlocked())

# ─── Test 10: password_gate.py integration ───────────────────
print("\n--- Test 10: password_gate integration ---")
profiles.reset_session()

import password_gate
import importlib
importlib.reload(password_gate)
# Reset password gate too
password_gate.reset_session()

test("password_gate.is_unlocked() starts False", not password_gate.is_unlocked())
test("profiles.is_cherry_unlocked() starts False", not profiles.is_cherry_unlocked())

# Unlock via password_gate
ok = password_gate.attempt_unlock(correct_pw)
test("password_gate.attempt_unlock() succeeds", ok)
test("password_gate.is_unlocked() is True", password_gate.is_unlocked())
test("profiles.is_cherry_unlocked() is True", profiles.is_cherry_unlocked())

# Skip via password_gate
password_gate.reset_session()
profiles.reset_session()

password_gate.skip_unlock()
test("password_gate.skip_unlock() locks gate", password_gate.is_locked_out())
test("profiles.is_cherry_unlocked() is False", not profiles.is_cherry_unlocked())
test("password_gate attempts left is 0", password_gate.attempts_left() == 0)

# ─── Summary ──────────────────────────────────────────────────
print("\n═══════════════════════════════════════════")
print(f"  RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
print("═══════════════════════════════════════════")

sys.exit(0 if FAIL == 0 else 1)
