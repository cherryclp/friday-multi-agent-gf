"""
FileAgent — read, write, list, move, search files on the system.
Args: {action: "read"|"write"|"list"|"move"|"search"|"create_dir", path: str, content: str = None}
"""

import os
import shutil
import glob
import json
import re

USER_HOME  = os.environ.get("USERPROFILE", os.path.expanduser("~"))
JARVIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUTPUT_DIR = os.path.join(JARVIS_DIR, "agent_outputs", "files")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Path aliases
PATH_ALIASES = {
    "desktop":   os.path.join(USER_HOME, "Desktop"),
    "downloads": os.path.join(USER_HOME, "Downloads"),
    "documents": os.path.join(USER_HOME, "Documents"),
    "pictures":  os.path.join(USER_HOME, "Pictures"),
    "videos":    os.path.join(USER_HOME, "Videos"),
    "music":     os.path.join(USER_HOME, "Music"),
    "jarvis":    JARVIS_DIR,
    "outputs":   OUTPUT_DIR,
}

def _resolve_path(path: str) -> str:
    """Resolve aliases and relative paths."""
    p = path.strip().strip('"\'')
    # Check alias
    for alias, full in PATH_ALIASES.items():
        if p.lower() == alias or p.lower().startswith(alias + "/") or p.lower().startswith(alias + "\\"):
            rest = p[len(alias):].lstrip("/\\")
            p = os.path.join(full, rest) if rest else full
            break
    # Expand ~ and env vars
    p = os.path.expandvars(os.path.expanduser(p))
    return os.path.normpath(p)


class FileAgent:
    def __init__(self, llm=None):
        self.llm = llm

    def _read(self, path: str) -> str:
        try:
            size = os.path.getsize(path)
            if size > 500_000:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(5000)
                return f"📄 {path} ({size:,} bytes — showing first 5000 chars):\n\n{content}\n[...truncated]"
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return f"📄 {path}:\n\n{content}"
        except PermissionError:
            return f"❌ Permission denied: {path}"
        except Exception as e:
            return f"❌ Read error: {e}"

    def _write(self, path: str, content: str) -> str:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"✅ Written to: {path} ({len(content)} chars)"
        except Exception as e:
            return f"❌ Write error: {e}"

    def _list(self, path: str) -> str:
        try:
            if not os.path.isdir(path):
                return f"❌ Not a directory: {path}"
            items = os.listdir(path)
            dirs  = sorted([i for i in items if os.path.isdir(os.path.join(path, i))])
            files = sorted([i for i in items if os.path.isfile(os.path.join(path, i))])
            lines = [f"📁 {path} ({len(dirs)} dirs, {len(files)} files):"]
            for d in dirs[:20]:
                lines.append(f"  📂 {d}/")
            for f in files[:30]:
                size = os.path.getsize(os.path.join(path, f))
                lines.append(f"  📄 {f} ({size:,}b)")
            if len(items) > 50:
                lines.append(f"  ... and {len(items)-50} more")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ List error: {e}"

    def _move(self, src: str, dst: str) -> str:
        try:
            if not os.path.exists(src):
                return f"❌ Source not found: {src}"
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.move(src, dst)
            return f"✅ Moved: {src} → {dst}"
        except Exception as e:
            return f"❌ Move error: {e}"

    def _search(self, path: str, pattern: str) -> str:
        try:
            matches = []
            search_path = os.path.join(path, "**", pattern)
            for match in glob.glob(search_path, recursive=True):
                matches.append(match)
                if len(matches) >= 20:
                    break
            if not matches:
                return f"❌ No files matching '{pattern}' in {path}"
            lines = [f"🔍 Found {len(matches)} match(es) for '{pattern}':"]
            for m in matches:
                lines.append(f"  {m}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Search error: {e}"

    def _create_dir(self, path: str) -> str:
        try:
            os.makedirs(path, exist_ok=True)
            return f"✅ Created directory: {path}"
        except Exception as e:
            return f"❌ mkdir error: {e}"

    def run(self, args: dict) -> str:
        action  = args.get("action", "list").lower()
        raw_path = args.get("path", "")
        content  = args.get("content", "")
        pattern  = args.get("pattern", "*")
        dst_path = args.get("destination", "")

        if not raw_path:
            return "ERROR: FileAgent needs a 'path' argument"

        path = _resolve_path(raw_path)

        if action == "read":
            return self._read(path)

        elif action == "write":
            if not content:
                return "ERROR: FileAgent needs 'content' for action=write"
            return self._write(path, content)

        elif action == "list":
            return self._list(path)

        elif action == "move":
            if not dst_path:
                return "ERROR: FileAgent needs 'destination' for action=move"
            return self._move(path, _resolve_path(dst_path))

        elif action == "search":
            return self._search(path, pattern)

        elif action == "create_dir":
            return self._create_dir(path)

        elif action == "delete":
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    return f"✅ Deleted file: {path}"
                elif os.path.isdir(path):
                    shutil.rmtree(path)
                    return f"✅ Deleted folder: {path}"
                else:
                    return f"❌ Not found: {path}"
            except Exception as e:
                return f"❌ Delete error: {e}"

        elif action == "append":
            if not content:
                return "ERROR: FileAgent needs 'content' for action=append"
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
                return f"✅ Appended {len(content)} chars to: {path}"
            except Exception as e:
                return f"❌ Append error: {e}"

        else:
            return f"ERROR: Unknown action '{action}'. Use: read, write, list, move, search, create_dir, delete, append"
