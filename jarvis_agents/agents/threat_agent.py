"""
ThreatAgent — scans for real threats: suspicious processes, network connections,
unknown startup entries, and suspicious files.
Args: {scan_type: "all"|"processes"|"network"|"startup"|"files", path: str = None}
"""

import subprocess
import os
import re
import json
from datetime import datetime

JARVIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Known suspicious process names (extend as needed)
SUSPICIOUS_PROCESSES = {
    "mimikatz", "pwdump", "fgdump", "wce", "gsecdump",
    "netcat", "ncat", "nc64", "psexec", "paexec",
    "meterpreter", "cobalt", "empire", "metasploit",
    "keylogger", "spyware", "ratty", "njrat", "darkcomet",
    "cryptominer", "xmrig", "minerd", "cpuminer",
}

# Known suspicious ports
SUSPICIOUS_PORTS = {
    1337, 4444, 5555, 6666, 6667, 7777, 8888, 9999,
    31337, 12345, 54321, 65535, 1080, 3389,
}

# Startup locations to check
STARTUP_KEYS = [
    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
    r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
    r"HKLM\Software\Microsoft\Windows\CurrentVersion\RunOnce",
]


def _run_ps(command: str, timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


class ThreatAgent:
    def __init__(self, llm=None):
        self.llm = llm
        self.threats_found = []

    def _scan_processes(self) -> str:
        """Get all running processes and flag suspicious ones."""
        ps_cmd = """
Get-Process | Select-Object Name, Id, CPU, WorkingSet, Path |
ForEach-Object {
    [PSCustomObject]@{
        Name = $_.Name
        PID  = $_.Id
        CPU  = [math]::Round($_.CPU, 1)
        RAM_MB = [math]::Round($_.WorkingSet / 1MB, 1)
        Path = if ($_.Path) { $_.Path } else { 'N/A' }
    }
} | ConvertTo-Json
"""
        raw = _run_ps(ps_cmd)
        flagged = []
        normal_count = 0

        try:
            procs = json.loads(raw) if raw.startswith("[") else [json.loads(raw)]
        except Exception:
            # Parse as text fallback
            lines = raw.split("\n")
            suspicious_lines = [l for l in lines if any(s in l.lower() for s in SUSPICIOUS_PROCESSES)]
            if suspicious_lines:
                return f"⚠️ Suspicious processes found:\n" + "\n".join(suspicious_lines)
            return f"✅ Processes scanned — nothing immediately suspicious ({len(lines)} processes running)"

        for proc in procs:
            name = str(proc.get("Name", "")).lower()
            path = str(proc.get("Path", "N/A"))
            pid  = proc.get("PID", "?")
            cpu  = proc.get("CPU", 0) or 0

            is_suspicious = False
            reasons = []

            # Check against known suspicious names
            if any(s in name for s in SUSPICIOUS_PROCESSES):
                is_suspicious = True
                reasons.append("known malicious name")

            # Check for processes with no path (might be injected)
            if path == "N/A" and name not in {"system", "idle", "registry", "memory compression",
                                               "secure system", "smss", "csrss", "wininit",
                                               "services", "lsass", "svchost", "fontdrvhost",
                                               "dwm", "conhost", "spoolsv"}:
                # Only flag if CPU is high with no path
                if float(cpu) > 50:
                    is_suspicious = True
                    reasons.append(f"no path + high CPU ({cpu}%)")

            # Check for processes running from temp dirs
            suspicious_dirs = ["\\temp\\", "\\tmp\\", "\\appdata\\local\\temp\\",
                                "%temp%", "\\downloads\\"]
            if any(sd in path.lower() for sd in suspicious_dirs):
                is_suspicious = True
                reasons.append(f"running from temp/downloads: {path}")

            if is_suspicious:
                flagged.append(f"  ⚠️ {proc.get('Name')} (PID {pid}) — {', '.join(reasons)}")
                self.threats_found.append({
                    "type": "process",
                    "name": proc.get("Name"),
                    "pid": pid,
                    "reasons": reasons,
                    "path": path,
                })
            else:
                normal_count += 1

        if flagged:
            result = f"⚠️ SUSPICIOUS PROCESSES ({len(flagged)} flagged):\n" + "\n".join(flagged)
        else:
            result = f"✅ Process scan clean — {normal_count} processes running, nothing suspicious"

        return result

    def _scan_network(self) -> str:
        """Check active network connections for suspicious ports/IPs."""
        ps_cmd = """
Get-NetTCPConnection -State Established,Listen |
Select-Object LocalPort, RemoteAddress, RemotePort, State, OwningProcess |
ConvertTo-Json
"""
        raw = _run_ps(ps_cmd)
        flagged = []

        try:
            conns = json.loads(raw) if raw.startswith("[") else ([json.loads(raw)] if raw.startswith("{") else [])
        except Exception:
            return f"Network scan raw output:\n{raw[:500]}"

        for conn in conns:
            remote_port  = int(conn.get("RemotePort", 0) or 0)
            local_port   = int(conn.get("LocalPort", 0) or 0)
            remote_addr  = str(conn.get("RemoteAddress", ""))
            state        = str(conn.get("State", ""))
            pid          = conn.get("OwningProcess", "?")

            reasons = []

            if remote_port in SUSPICIOUS_PORTS:
                reasons.append(f"suspicious remote port {remote_port}")
            if local_port in SUSPICIOUS_PORTS:
                reasons.append(f"suspicious local port {local_port}")

            # Flag connections to uncommon foreign IPs on unusual ports
            if remote_addr and remote_addr not in ("0.0.0.0", "127.0.0.1", "::", "::1", ""):
                if remote_port > 49000 and state == "Established":
                    reasons.append(f"high ephemeral port established to {remote_addr}")

            if reasons:
                flagged.append(f"  ⚠️ {remote_addr}:{remote_port} (local:{local_port}, PID:{pid}) — {', '.join(reasons)}")
                self.threats_found.append({
                    "type": "network",
                    "remote": f"{remote_addr}:{remote_port}",
                    "reasons": reasons,
                })

        if flagged:
            return f"⚠️ SUSPICIOUS CONNECTIONS ({len(flagged)}):\n" + "\n".join(flagged)

        total = len(conns)
        return f"✅ Network scan clean — {total} connections checked, none suspicious"

    def _scan_startup(self) -> str:
        """Check Windows startup entries for unknown programs."""
        flagged = []
        results = []

        for key in STARTUP_KEYS:
            ps_cmd = f'Get-ItemProperty -Path "Registry::{key}" -ErrorAction SilentlyContinue | ConvertTo-Json'
            raw = _run_ps(ps_cmd)
            if "ERROR" in raw or not raw:
                continue
            try:
                entries = json.loads(raw)
                if isinstance(entries, dict):
                    for name, val in entries.items():
                        if name.startswith("PS") or name in ("(default)",):
                            continue
                        val_str = str(val).lower()
                        reasons = []
                        if any(d in val_str for d in ["\\temp\\", "\\tmp\\", "%temp%", "\\downloads\\"]):
                            reasons.append("runs from temp/downloads")
                        if re.search(r'\.(vbs|bat|cmd|ps1|py|exe)\b', val_str) and "\\windows\\" not in val_str and "\\program files" not in val_str:
                            reasons.append("script in non-standard location")
                        if reasons:
                            flagged.append(f"  ⚠️ [{key}] {name} = {val} — {', '.join(reasons)}")
                        else:
                            results.append(f"  ✓ {name}: {str(val)[:60]}")
            except Exception:
                results.append(f"  (could not parse {key})")

        if flagged:
            return f"⚠️ SUSPICIOUS STARTUP ENTRIES:\n" + "\n".join(flagged) + \
                   (f"\n\nNormal entries:\n" + "\n".join(results[:5]) if results else "")
        return f"✅ Startup scan clean\nEntries found:\n" + "\n".join(results[:10])

    def _scan_files(self, scan_path: str = None) -> str:
        """Scan a directory for suspicious files."""
        if not scan_path:
            scan_path = os.path.join(os.environ.get("USERPROFILE", "~"), "Downloads")

        suspicious_exts = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".scr", ".pif"}
        double_ext      = re.compile(r'\.(pdf|doc|docx|jpg|png|mp4)\.(exe|bat|cmd|ps1|vbs)$', re.I)

        flagged = []
        scanned = 0

        try:
            for root, dirs, files in os.walk(scan_path):
                dirs[:] = [d for d in dirs if not d.startswith(".")]  # skip hidden
                for fname in files:
                    scanned += 1
                    fpath = os.path.join(root, fname)
                    _, ext = os.path.splitext(fname.lower())
                    reasons = []

                    if double_ext.search(fname.lower()):
                        reasons.append("double extension (disguised file)")
                    elif ext in suspicious_exts:
                        # Check if it was recently created (last 7 days)
                        try:
                            age_days = (datetime.now().timestamp() - os.path.getmtime(fpath)) / 86400
                            if age_days < 7:
                                reasons.append(f"executable created {age_days:.1f} days ago")
                        except Exception:
                            pass

                    if reasons:
                        flagged.append(f"  ⚠️ {fpath} — {', '.join(reasons)}")

                if scanned > 500:
                    break  # Don't hang forever

        except PermissionError:
            pass
        except Exception as e:
            return f"❌ File scan error: {e}"

        if flagged:
            return f"⚠️ SUSPICIOUS FILES in {scan_path} ({len(flagged)} flagged):\n" + "\n".join(flagged[:15])
        return f"✅ File scan clean — {scanned} files checked in {scan_path}"

    def run(self, args: dict) -> str:
        scan_type = args.get("scan_type", "all").lower()
        path      = args.get("path", None)

        self.threats_found = []
        results = []
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        results.append(f"🔍 **Jarvis Threat Scan** — {timestamp}")
        results.append("=" * 50)

        if scan_type in ("all", "processes"):
            results.append("\n**Process Scan:**")
            results.append(self._scan_processes())

        if scan_type in ("all", "network"):
            results.append("\n**Network Scan:**")
            results.append(self._scan_network())

        if scan_type in ("all", "startup"):
            results.append("\n**Startup Scan:**")
            results.append(self._scan_startup())

        if scan_type in ("all", "files"):
            results.append("\n**File Scan:**")
            results.append(self._scan_files(path))

        results.append("=" * 50)
        total_threats = len(self.threats_found)
        if total_threats > 0:
            results.append(f"⚠️ **TOTAL: {total_threats} potential threat(s) found — review above**")
            # Save report
            report_path = os.path.join(JARVIS_DIR, "agent_outputs", f"threat_report_{int(datetime.now().timestamp())}.txt")
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            try:
                with open(report_path, "w") as f:
                    f.write("\n".join(results))
                results.append(f"📄 Report saved: {report_path}")
            except Exception:
                pass
        else:
            results.append("✅ **All clear — no threats detected**")

        return "\n".join(results)
