"""
ShellAgent — runs PowerShell/CMD commands directly on the system.
Args: {command: str, shell: str = "powershell", timeout: int = 30}
"""

import subprocess
import os
import re

JARVIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Commands that are too dangerous to run
BLOCKED_PATTERNS = [
    r"format\s+[a-z]:", r"del\s+/s\s+/q\s+c:\\",
    r"rd\s+/s\s+/q\s+c:\\windows",
    r"rm\s+-rf\s+/", r"dd\s+if=",
    r":()\s*\{.*\}", r"fork\s*bomb",
]

def _is_dangerous(command: str) -> bool:
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower):
            return True
    return False


class ShellAgent:
    def __init__(self, llm=None):
        self.llm = llm

    def _resolve_command(self, task: str) -> str:
        """If task is natural language (not a command), convert it."""
        # If it looks like a command already, run it directly
        command_indicators = ["pip ", "npm ", "python ", "powershell", "cmd ", "cd ",
                              "mkdir", "copy", "move", "del ", "echo ", "dir ", "ls ",
                              "git ", "choco ", "winget ", "set ", "netstat", "tasklist",
                              "ipconfig", "curl ", "wget ", "invoke-"]
        if any(task.lower().startswith(c) for c in command_indicators):
            return task

        # Otherwise ask LLM to generate the command
        if self.llm:
            system = (
                "You are a Windows PowerShell expert. Convert the user's request into "
                "a single PowerShell command or short script. "
                "Output ONLY the command — nothing else, no explanation, no backticks.\n"
                "Use PowerShell syntax. For pip commands use: pip install X --quiet"
            )
            try:
                cmd = self.llm.invoke([
                    ("system", system),
                    ("user", task)
                ]).content.strip()
                # Strip fences
                if cmd.startswith("```"):
                    lines = cmd.split("\n")
                    cmd = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
                return cmd.strip()
            except Exception:
                pass
        return task

    def run(self, args: dict) -> str:
        command = args.get("command", "")
        context = args.get("_context", "")  # from depends_on
        shell   = args.get("shell", "powershell").lower()
        timeout = int(args.get("timeout", 30))

        if not command:
            return "ERROR: ShellAgent needs a 'command' argument"

        # If command is natural language, resolve it
        if context:
            command = command + f"\n# Context: {context}"

        # Resolve natural language to actual command
        resolved = self._resolve_command(command)

        # Safety check
        if _is_dangerous(resolved):
            return f"❌ BLOCKED: Command flagged as dangerous: {resolved[:100]}"

        try:
            if shell in ("powershell", "ps", "ps1"):
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", resolved],
                    capture_output=True, text=True, timeout=timeout,
                    cwd=JARVIS_DIR
                )
            elif shell in ("cmd", "batch", "bat"):
                result = subprocess.run(
                    resolved, shell=True,
                    capture_output=True, text=True, timeout=timeout,
                    cwd=JARVIS_DIR
                )
            else:
                result = subprocess.run(
                    resolved, shell=True,
                    capture_output=True, text=True, timeout=timeout
                )

            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()

            if result.returncode == 0:
                return f"✅ Command succeeded.\n{out}" if out else "✅ Done (no output)."
            else:
                full_err = "\n".join(filter(None, [out, err]))
                return f"❌ Exit {result.returncode}:\n{full_err[:500]}"

        except subprocess.TimeoutExpired:
            return f"⏱️ Command timed out after {timeout}s"
        except Exception as e:
            return f"❌ ShellAgent error: {e}"
