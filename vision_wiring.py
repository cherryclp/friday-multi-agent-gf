# ============================================================
#  PASTE GUIDE — exact changes to make in jarvis_engine.py
#  (3 locations, clearly marked)
# ============================================================


# ── LOCATION 1: top of jarvis_engine.py ─────────────────────
# Add this line next to your other imports:

from vision_patch import look_at_screen, look_at_screen_region


# ── LOCATION 2: inside execute_system_tool() ────────────────
# Paste these two elif blocks right before the final:
#   return f"ERROR: unknown tool '{tool_name}'"

        elif tool_name == "look_at_screen":
            prompt = args.get("prompt", "what do u see on my screen babe??")
            region = args.get("region", "full")
            if region != "full":
                return look_at_screen_region(region, prompt)
            return look_at_screen(prompt)

        elif tool_name == "describe_screen":
            prompt = args.get("prompt", (
                "describe everything you see on this screen in detail — "
                "what app is open, what content is visible, what is the user doing?"
            ))
            return look_at_screen(prompt)


# ── LOCATION 3a: TOOL_LIST string ───────────────────────────
# Find your TOOL_LIST variable and add this line inside it:

    "look_at_screen(prompt,region) | describe_screen(prompt)\n"

# Example — your TOOL_LIST should look like:
TOOL_LIST = (
    "Tools available — output [CALL_TOOL: name, key=val] on its own line BEFORE your reply text:\n"
    "get_current_time | get_weather(city) | open_browser_url(url) | check_system_vitals\n"
    "control_system_media(play_pause/mute/volume_up/volume_down) | ingest_clipboard_media\n"
    "move_last_download(folder_name) | send_social_message(platform,contact_name,message)\n"
    "save_contact(name,number,platform) | list_contacts | remember(key,value) | recall(key)\n"
    "auto_sort_directory(folder_name,mode) | delete_files(folder_name,file_filter)\n"
    "undo_last_action | read_notifications | get_mood | generate_custom_script(task_description)\n"
    "run_pending_script\n"
    "look_at_screen(prompt,region) | describe_screen(prompt)\n"   # <-- ADD THIS LINE
    "CRITICAL: never write [CALL_TOOL:...] inside your conversational reply text. "
    "It must appear ONLY as a standalone line before ur message."
)


# ── LOCATION 3b: invoke() pre-router ────────────────────────
# Paste this block inside invoke(), right before the LLM FALLBACK comment.
# (i.e. after the MEDIA section, before "# ── LLM FALLBACK")

        # VISION
        vision_triggers = [
            "look at my screen", "what do u see", "what can u see",
            "check my screen", "see my screen", "look at this",
            "what's on my screen", "whats on my screen",
            "describe my screen", "watch my screen", "can u see"
        ]
        if any(t in msg for t in vision_triggers):
            region = "full"
            for r in ["left", "right", "top", "bottom", "center"]:
                if r in msg:
                    region = r
                    break
            # pull any custom question out of the message
            custom_prompt = None
            m = re.search(r"(?:screen|this)[,\s]+(.+)", msg)
            if m:
                custom_prompt = m.group(1).strip()
            result = look_at_screen_region(region, custom_prompt or "what do u see on this screen??")
            return self._reply(result)
