"""
JARVIS ENGINE PATCH — Multi-Agent Orchestrator Hook
====================================================
Add this to jarvis_engine.py in TWO places:

──────────────────────────────────────────────────────────────
PLACE 1: Near the top of jarvis_engine.py, after the imports
(around line 35, after "from langchain_openai import ChatOpenAI")
──────────────────────────────────────────────────────────────

from agent_orchestrator import run_agent_goal, should_use_agents


──────────────────────────────────────────────────────────────
PLACE 2: Inside ManualJarvisAgent.invoke(), BEFORE the
"# ── LLM FALLBACK with mood-aware prompt" section
(search for "# ── LLM FALLBACK" and paste this block above it)
──────────────────────────────────────────────────────────────

        # ── MULTI-AGENT ORCHESTRATOR ─────────────────────────────────────────
        # Fires when user wants something DONE, not just talked about
        if should_use_agents(user_message):
            try:
                agent_result = run_agent_goal(
                    user_message,
                    llm,
                    status_callback=_yap_callback  # streams progress to UI
                )
                if agent_result:
                    return self._reply(agent_result)
                # If returns None, falls through to normal LLM response
            except Exception as e:
                print(f"[ORCHESTRATOR] error: {e}")
                # Don't crash — fall through to LLM
        # ─────────────────────────────────────────────────────────────────────


That's it. Two edits. The rest of jarvis_engine.py stays exactly the same.

HOW IT WORKS:
- User says "build me a python script that monitors my CPU and alerts me"
  → should_use_agents() returns True
  → Orchestrator plans: [CodeAgent(write script), ShellAgent(install psutil), CodeAgent(run it)]
  → Agents execute in order
  → Result returned to UI

- User says "hey what's the weather"
  → should_use_agents() returns False
  → Normal flow, existing pre-router handles it

TRIGGER PHRASES (in should_use_agents):
"do it", "get it done", "actually do", "make it happen", "execute",
"build me", "create and run", "write and run", "set up", "install and",
"research and", "find and", "scan for", "check for threats",
"automate", "complete the task", "do the following", "i need u to",
"can u actually", "handle", "take care of"

Also fires for long messages with: "and then", "after that", "also", "then", "finally"
"""

# ═══════════════════════════════════════════════════════════════
# QUICK TEST (run this standalone to verify agents work)
# python -c "from agents.shell_agent import ShellAgent; a=ShellAgent(); print(a.run({'command':'echo hello world'}))"
# python -c "from agents.threat_agent import ThreatAgent; a=ThreatAgent(); print(a.run({'scan_type':'processes'}))"
# ═══════════════════════════════════════════════════════════════
