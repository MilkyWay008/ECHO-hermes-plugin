"""ECHO HookRegistry handler — launches the watchdog on gateway:startup.

The watchdog is started for the default agent only. Once running, it
discovers all profiles with state.db files and monitors them all.

Checks for stale .watchdog-default.pid before launching to prevent
duplicate instances on unexpected restarts.
"""

import os
import subprocess
import sys


def handle(event_type: str, context: dict) -> None:
    """Handle gateway:startup — launch the ECHO watchdog."""
    if event_type != "gateway:startup":
        return

    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    agent_name = "default"
    parent_pid = os.getpid()
    poll_script = os.path.join(hermes_home, "plugins", "echo-context-health", "echo-poll.py")

    if not os.path.exists(poll_script):
        return

    # Simple guard: if .watchdog-default.pid exists and that PID is alive, skip
    pid_file = os.path.join(os.path.expanduser("~/.hermes"), "temp", "echo", f".watchdog-{agent_name}.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            if sys.platform == "win32":
                import ctypes
                SYNCHRONIZE = 0x00100000
                handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, old_pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return  # Watchdog already running
            else:
                try:
                    os.kill(old_pid, 0)
                    return  # Watchdog already running
                except OSError:
                    pass  # Dead PID, proceed
        except Exception:
            pass  # Stale PID file, proceed

    args = [sys.executable or "python", poll_script, "--agent", agent_name,
            "--parent-pid", str(parent_pid)]

    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
