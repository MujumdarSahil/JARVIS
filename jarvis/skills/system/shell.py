"""
Subprocess and system metrics via psutil with guardrails and logging.
"""

from __future__ import annotations

import datetime as dt
import platform
import re
from pathlib import Path
import subprocess
import time
from typing import Any

import psutil
import socket

from utils.logger import get_logger

logger = get_logger(__name__)

_MAX_OUT = 4000


def _truncate(s: str) -> str:
    if len(s) <= _MAX_OUT:
        return s
    return s[:_MAX_OUT] + "\n... [output truncated]"


def _shell_blocked(command: str) -> str | None:
    """Return error message if command matches blocked patterns."""
    c = (command or "").strip()
    if not c:
        return "Empty command"
    low = c.lower()
    if "rm -rf /" in low or "rm -rf /*" in low:
        return "Blocked: destructive rm pattern"
    if "mkfs" in low:
        return "Blocked: mkfs"
    if "fdisk" in low:
        return "Blocked: fdisk"
    if "deltree" in low:
        return "Blocked: deltree"
    if re.search(r"\bdd\b", low):
        return "Blocked: dd"
    if re.search(r"\bformat\b", low):
        return "Blocked: format"
    return None


class ShellSkill:
    """Run shell commands and report system/network state."""

    def run(self, command: str, timeout: int = 30, cwd: str | None = None) -> dict[str, Any]:
        try:
            block = _shell_blocked(command)
            if block:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": block,
                    "returncode": -1,
                    "duration": 0.0,
                }
            ts = dt.datetime.now().isoformat(timespec="seconds")
            logger.info("SHELL_RUN ts=%s cwd=%s cmd=%s", ts, cwd, command)
            t0 = time.perf_counter()
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=max(1, int(timeout)),
            )
            dur = time.perf_counter() - t0
            return {
                "success": proc.returncode == 0,
                "stdout": _truncate(proc.stdout or ""),
                "stderr": _truncate(proc.stderr or ""),
                "returncode": proc.returncode,
                "duration": round(dur, 3),
            }
        except subprocess.TimeoutExpired as e:
            out = _truncate((e.stdout or "") if isinstance(e.stdout, str) else "")
            err = _truncate((e.stderr or "") if isinstance(e.stderr, str) else "timeout")
            return {
                "success": False,
                "stdout": out,
                "stderr": err + "\nCommand timed out",
                "returncode": -1,
                "duration": float(timeout),
            }
        except Exception as e:
            logger.exception("shell.run failed: %s", e)
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "duration": 0.0,
            }

    def run_powershell(self, script: str, timeout: int = 30) -> dict[str, Any]:
        try:
            block = _shell_blocked(script)
            if block:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": block,
                    "returncode": -1,
                    "duration": 0.0,
                }
            ts = dt.datetime.now().isoformat(timespec="seconds")
            logger.info("SHELL_POWERSHELL ts=%s script=%s", ts, script)
            t0 = time.perf_counter()
            if platform.system() == "Windows":
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout)),
                )
            else:
                proc = subprocess.run(
                    ["/bin/bash", "-c", script],
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(timeout)),
                )
            dur = time.perf_counter() - t0
            return {
                "success": proc.returncode == 0,
                "stdout": _truncate(proc.stdout or ""),
                "stderr": _truncate(proc.stderr or ""),
                "returncode": proc.returncode,
                "duration": round(dur, 3),
            }
        except subprocess.TimeoutExpired as e:
            out = _truncate((e.stdout or "") if isinstance(e.stdout, str) else "")
            err = _truncate((e.stderr or "") if isinstance(e.stderr, str) else "timeout")
            return {
                "success": False,
                "stdout": out,
                "stderr": err + "\nCommand timed out",
                "returncode": -1,
                "duration": float(timeout),
            }
        except Exception as e:
            logger.exception("run_powershell failed: %s", e)
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "duration": 0.0,
            }

    def get_running_processes(self) -> dict[str, Any]:
        try:
            procs: list[psutil.Process] = []
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    procs.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            for p in procs:
                try:
                    p.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(0.15)
            rows: list[tuple[float, str, int]] = []
            for p in procs:
                try:
                    cpu = p.cpu_percent(interval=None)
                    name = p.name() or "?"
                    rows.append((cpu, name, p.pid))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            rows.sort(key=lambda x: -x[0])
            top = rows[:20]
            lines = [f"{cpu:6.1f}%  pid={pid:6}  {name}" for cpu, name, pid in top]
            return {
                "success": True,
                "stdout": "\n".join(lines) if lines else "(no processes)",
                "stderr": "",
                "returncode": 0,
                "duration": 0.0,
            }
        except Exception as e:
            logger.exception("get_running_processes failed: %s", e)
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "duration": 0.0,
            }

    def kill_process(self, name_or_pid: str) -> dict[str, Any]:
        try:
            raw = (name_or_pid or "").strip()
            if not raw:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": "name_or_pid required",
                    "returncode": -1,
                    "duration": 0.0,
                }
            ts = dt.datetime.now().isoformat(timespec="seconds")
            logger.info("KILL_PROCESS ts=%s target=%s", ts, raw)
            killed: list[str] = []
            if raw.isdigit():
                pid = int(raw)
                p = psutil.Process(pid)
                p.terminate()
                gone, alive = psutil.wait_procs([p], timeout=3)
                for a in alive:
                    a.kill()
                killed.append(f"pid {pid}")
            else:
                name = raw.lower()
                for p in psutil.process_iter(["pid", "name"]):
                    try:
                        pname = (p.name() or "").lower()
                        if name in pname or pname == name:
                            p.terminate()
                            killed.append(f"{p.name()} ({p.pid})")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                if killed:
                    time.sleep(0.2)
            msg = f"Terminated: {', '.join(killed)}" if killed else "No matching process"
            return {
                "success": bool(killed),
                "stdout": msg,
                "stderr": "" if killed else "No process matched",
                "returncode": 0 if killed else 1,
                "duration": 0.0,
            }
        except Exception as e:
            logger.exception("kill_process failed: %s", e)
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "duration": 0.0,
            }

    def get_system_info(self) -> dict[str, Any]:
        try:
            cpu = psutil.cpu_percent(interval=0.2)
            vm = psutil.virtual_memory()
            du = None
            if platform.system() == "Windows":
                for drive in ("C:\\", f"{Path.home().drive}\\"):
                    try:
                        du = psutil.disk_usage(drive)
                        break
                    except OSError:
                        continue
                if du is None:
                    du = psutil.disk_usage(".")
            else:
                du = psutil.disk_usage("/")
            batt = psutil.sensors_battery()
            boot = psutil.boot_time()
            uptime_s = time.time() - boot
            batt_txt = "n/a"
            if batt is not None:
                batt_txt = f"{batt.percent}% plugged={batt.power_plugged}"
            lines = [
                f"cpu_percent: {cpu}",
                f"ram_percent: {vm.percent}",
                f"ram_used_mb: {vm.used // (1024 * 1024)}",
                f"disk_percent: {du.percent}",
                f"disk_free_gb: {du.free / (1024 ** 3):.2f}",
                f"battery: {batt_txt}",
                f"uptime_seconds: {int(uptime_s)}",
            ]
            text = "\n".join(lines)
            return {
                "success": True,
                "stdout": text,
                "stderr": "",
                "returncode": 0,
                "duration": 0.0,
            }
        except Exception as e:
            logger.exception("get_system_info failed: %s", e)
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "duration": 0.0,
            }

    def get_network_info(self) -> dict[str, Any]:
        try:
            addrs = psutil.net_if_addrs()
            ip_lines: list[str] = []
            for iface, plist in addrs.items():
                for a in plist:
                    if getattr(a, "family", None) == socket.AF_INET:
                        ip_lines.append(f"{iface}: {a.address}")
            try:
                conns = len(psutil.net_connections(kind="inet"))
            except (psutil.AccessDenied, PermissionError):
                conns = -1
            io = psutil.net_io_counters()
            sent = io.bytes_sent if io else 0
            recv = io.bytes_recv if io else 0
            lines = [
                "addresses:",
                *(ip_lines or ["(none)"]),
                f"active_connections: {conns}",
                f"bytes_sent: {sent}",
                f"bytes_recv: {recv}",
            ]
            text = "\n".join(lines)
            return {
                "success": True,
                "stdout": text,
                "stderr": "",
                "returncode": 0,
                "duration": 0.0,
            }
        except Exception as e:
            logger.exception("get_network_info failed: %s", e)
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "duration": 0.0,
            }


if __name__ == "__main__":
    s = ShellSkill()
    print(s.get_system_info())
    print(s.run("echo hello", timeout=5))
