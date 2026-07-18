"""
agent_orchestrator.py — Jarvis Multi-Agent Brain
Jarvis receives a goal → breaks it into tasks → spawns agents → collects results → acts.

Drop this file into your JARVIS project root.
Usage in jarvis_engine.py:
    from agent_orchestrator import run_agent_goal
    # call run_agent_goal(user_message, llm) from inside invoke()
"""

import json
import threading
import time
import traceback
from typing import Callable

# ── Sub-agent imports ──────────────────────────────────────────
from agents.code_agent    import CodeAgent
from agents.shell_agent   import ShellAgent
from agents.browser_agent import BrowserAgent
from agents.file_agent    import FileAgent
from agents.threat_agent  import ThreatAgent
from agents.research_agent import ResearchAgent

# ── Available agents registry ─────────────────────────────────
AGENT_REGISTRY = {
    "code":     CodeAgent,
    "shell":    ShellAgent,
    "browser":  BrowserAgent,
    "file":     FileAgent,
    "threat":   ThreatAgent,
    "research": ResearchAgent,
}

AGENT_DESCRIPTIONS = """
Available agents you can spawn:
- code:     Write, run, debug, or fix Python/JS/any code. Args: {task, language?, run?}
- shell:    Run PowerShell/CMD commands, install packages, control system. Args: {command}
- browser:  Open URLs, search web, scrape page content. Args: {action, url?, query?}
- file:     Read, write, list, move files and folders. Args: {action, path, content?}
- threat:   Scan for suspicious processes, network connections, unknown files. Args: {scan_type?}
- research: Search and summarize info from the web. Args: {query, depth?}
"""

# ── Orchestrator ───────────────────────────────────────────────

ORCHESTRATOR_SYSTEM = """You are Jarvis, an AI orchestrator. When the user gives you a complex goal,
you MUST break it into concrete sub-tasks and assign them to specialized agents.

{agent_descriptions}

You MUST respond with ONLY a JSON plan like this:
{{
  "goal_summary": "what the user wants in one line",
  "requires_agents": true,
  "tasks": [
    {{
      "id": 1,
      "agent": "shell",
      "description": "Install required packages",
      "args": {{"command": "pip install requests beautifulsoup4"}}
    }},
    {{
      "id": 2,
      "agent": "code",
      "description": "Write scraper script",
      "args": {{"task": "Write a Python script that scrapes...", "language": "python", "run": true}},
      "depends_on": [1]
    }}
  ]
}}

If the request is simple (just chat/question), respond with:
{{"requires_agents": false, "reason": "simple request"}}

Rules:
- Be specific with args — agents need exact instructions
- Use depends_on when a task needs another task's output
- Max 6 tasks per plan
- shell agent for system commands, code agent for writing/running code
"""


def _plan_tasks(user_message: str, llm) -> dict:
    """Ask the LLM to create an agent task plan."""
    system = ORCHESTRATOR_SYSTEM.format(agent_descriptions=AGENT_DESCRIPTIONS)
    try:
        response = llm.invoke([
            ("system", system),
            ("user", f"Goal: {user_message}")
        ]).content.strip()

        # Strip markdown fences if present
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        response = response.strip()

        return json.loads(response)
    except Exception as e:
        print(f"[ORCHESTRATOR] plan error: {e}")
        return {"requires_agents": False, "reason": str(e)}


def _execute_task(task: dict, results: dict, llm) -> str:
    """Execute a single agent task."""
    agent_name = task.get("agent", "shell")
    args       = task.get("args", {})
    task_id    = task.get("id", 0)
    desc       = task.get("description", "")

    # Inject previous task output if this task depends on it
    depends_on = task.get("depends_on", [])
    if depends_on:
        context_parts = []
        for dep_id in depends_on:
            dep_result = results.get(dep_id, "")
            if dep_result:
                context_parts.append(f"Output from task {dep_id}:\n{dep_result}")
        if context_parts:
            context = "\n\n".join(context_parts)
            # Inject into args
            if "task" in args:
                args["task"] = args["task"] + f"\n\nContext from previous steps:\n{context}"
            elif "command" in args:
                args["_context"] = context
            else:
                args["_context"] = context

    AgentClass = AGENT_REGISTRY.get(agent_name)
    if not AgentClass:
        return f"ERROR: Unknown agent '{agent_name}'"

    print(f"[ORCHESTRATOR] → Task {task_id}: [{agent_name}] {desc}")
    try:
        agent  = AgentClass(llm=llm)
        result = agent.run(args)
        print(f"[ORCHESTRATOR] ✓ Task {task_id} done: {str(result)[:100]}")
        return result
    except Exception as e:
        err = f"ERROR in {agent_name} agent: {str(e)}\n{traceback.format_exc()[:300]}"
        print(f"[ORCHESTRATOR] ✗ Task {task_id}: {err}")
        return err


def _execute_plan(plan: dict, llm) -> str:
    """Execute the full task plan respecting dependencies."""
    tasks   = plan.get("tasks", [])
    results = {}  # task_id -> output string
    summary_parts = []

    # Execute tasks in dependency order (simple topological sort)
    remaining   = list(tasks)
    max_rounds  = len(tasks) + 1
    rounds      = 0

    while remaining and rounds < max_rounds:
        rounds += 1
        executed_this_round = []

        for task in remaining:
            depends_on = task.get("depends_on", [])
            # Check all dependencies are satisfied
            if all(dep_id in results for dep_id in depends_on):
                result = _execute_task(task, results, llm)
                results[task["id"]] = result
                summary_parts.append(
                    f"**{task.get('description', f'Task {task[\"id\"]}')}**\n{result}"
                )
                executed_this_round.append(task)

        for t in executed_this_round:
            remaining.remove(t)

        if not executed_this_round:
            # Circular dependency or stuck
            for t in remaining:
                results[t["id"]] = "SKIPPED: dependency not resolved"
            break

    # Final LLM summary
    goal    = plan.get("goal_summary", "")
    raw_out = "\n\n".join(summary_parts)

    try:
        summary_prompt = (
            f"The user asked: \"{goal}\"\n\n"
            f"Agents executed these tasks:\n{raw_out}\n\n"
            "Summarize what was accomplished in 2-3 sentences in Jarvis's casual gf tone. "
            "Be specific about what actually got done. If something failed, say so clearly."
        )
        from jarvis_engine import get_mood_system_prompt
        mood = get_mood_system_prompt()
        final = llm.invoke([
            ("system", mood),
            ("user", summary_prompt)
        ]).content.strip()
        return final + f"\n\n---\n**Full output:**\n{raw_out}"
    except Exception:
        return f"Done! Here's what happened:\n\n{raw_out}"


# ── Public API ─────────────────────────────────────────────────

AGENTIC_TRIGGERS = [
    "do it", "get it done", "actually do", "make it happen", "execute",
    "build me", "create and run", "write and run", "set up", "install and",
    "research and", "find and", "download and", "scan for", "check for threats",
    "automate", "complete the task", "do the following", "i need u to",
    "can u actually", "handle", "take care of",
]

def should_use_agents(user_message: str) -> bool:
    """Detect if this message needs multi-agent execution."""
    msg = user_message.lower()
    return any(t in msg for t in AGENTIC_TRIGGERS) or (
        len(user_message) > 80 and any(
            w in msg for w in ["and then", "after that", "also", "then", "finally", "first"]
        )
    )


def run_agent_goal(user_message: str, llm, status_callback: Callable = None) -> str:
    """
    Main entry point. Call this from jarvis_engine.py invoke().
    status_callback: optional fn(str) to send progress updates to UI
    """
    if status_callback:
        status_callback("🤖 planning tasks...")

    plan = _plan_tasks(user_message, llm)

    if not plan.get("requires_agents", False):
        return None  # Fall through to normal LLM response

    tasks = plan.get("tasks", [])
    if not tasks:
        return None

    goal = plan.get("goal_summary", user_message)
    if status_callback:
        status_callback(f"📋 {len(tasks)} tasks planned: {goal}")

    result = _execute_plan(plan, llm)
    return result
