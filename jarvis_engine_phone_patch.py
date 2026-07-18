# ============================================================
#  JARVIS ENGINE — PHONE CONTROL PATCH 📱
#
#  This file shows the 3 changes you need to make in
#  jarvis_engine.py to enable Android phone control.
#
#  CHANGE 1: Add the import at the top of jarvis_engine.py
#  CHANGE 2: Add phone tools to execute_system_tool()
#  CHANGE 3: Add phone tools to TOOL_LIST and CRITICAL_TOOLS
# ============================================================


# ════════════════════════════════════════════════════════════
#  CHANGE 1 — Add this import near the top of jarvis_engine.py
#  (after the other imports, e.g. after "from vision_patch import...")
# ════════════════════════════════════════════════════════════

"""
from phone_control import execute_phone_tool, PHONE_TOOL_LIST
"""


# ════════════════════════════════════════════════════════════
#  CHANGE 2 — Add this block inside execute_system_tool()
#
#  Find the line:  return f"ERROR: unknown tool '{tool_name}'"
#  (it's near the very end of execute_system_tool)
#  Add the phone block ABOVE that line:
# ════════════════════════════════════════════════════════════

"""
        # ── PHONE TOOLS ───────────────────────────────────────────
        elif tool_name.startswith("phone_"):
            return execute_phone_tool(tool_name, args)
"""


# ════════════════════════════════════════════════════════════
#  CHANGE 3a — Add phone_call and phone_whatsapp to CRITICAL_TOOLS
#  in password_gate.py
#
#  Find:  CRITICAL_TOOLS = {
#  Add:   "phone_call",
#         "phone_whatsapp",
# ════════════════════════════════════════════════════════════

"""
CRITICAL_TOOLS = {
    "delete_files",
    "send_social_message",
    "run_pending_script",
    "generate_custom_script",
    "auto_sort_directory",
    "move_last_download",
    "ingest_clipboard_media",
    "enroll_face",
    "phone_call",        # ← NEW
    "phone_whatsapp",    # ← NEW
}
"""


# ════════════════════════════════════════════════════════════
#  CHANGE 3b — Add phone tools to TOOL_LIST in jarvis_engine.py
#
#  Find:  TOOL_LIST = (
#  At the end of the string, before the closing parenthesis,
#  add:   + PHONE_TOOL_LIST
#  So the last line becomes:
# ════════════════════════════════════════════════════════════

"""
TOOL_LIST = (
    "You have tools. To use one, output a line in EXACTLY this format (nothing else on that line):\n"
    "[CALL_TOOL: tool_name, arg_name=arg_value]\n"
    # ... all the existing tools ...
    "- If user says open/go to/launch ANY website or app, always use open_browser_url. No hesitation. No disclaimers."
) + PHONE_TOOL_LIST    # ← ADD THIS
"""


# ════════════════════════════════════════════════════════════
#  CHANGE 4 — Add phone triggers to the pre-router in invoke()
#
#  Find the section:  # ── SCREEN VISION — only fires for explicit screen phrases
#  Add this block BEFORE it (after the webcam section):
# ════════════════════════════════════════════════════════════

"""
        # ── PHONE COMMANDS — explicit "on my phone" trigger ──────────────────
        PHONE_TRIGGERS = [
            "on my phone", "on the phone", "my phone's", "phone volume",
            "phone battery", "phone screenshot", "lock my phone",
            "call ", "open on phone", "send on phone",
        ]
        if any(t in msg for t in PHONE_TRIGGERS):
            # Route through execute_system_tool with LLM picking the right phone tool
            from phone_control import execute_phone_tool
            # Let the LLM figure out which phone tool to use
            # (falls through to LLM fallback which has PHONE_TOOL_LIST in context)
            pass   # LLM will pick it up from TOOL_LIST context
"""
