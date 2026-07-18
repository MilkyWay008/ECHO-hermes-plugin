#!/usr/bin/env python3
"""ECHO Watchdog — launched by the gateway HookRegistry on gateway:startup.

Two modes:
  1. Watchdog mode (default): Long-running loop. Polls state.db every 30s,
     for BOTH the default agent AND all profile agents with a state.db.
     Fires ECHO when any session crosses threshold.

  2. One-shot mode (--recite-poll): Original echo-poll logic. Polls for a
     single recite file, runs compare, writes verdict, waits 60s, cleans up.
     Used when ECHO fires — launched as a subprocess from the watchdog.

Arguments:
  Watchdog mode: --agent <agent_name> --parent-pid <pid>
  One-shot mode: --recite-poll <session_id> <unique_suffix> <hermes_home> [profile]
"""

import sys
import os
import time
import subprocess
import glob
import json
import sqlite3

# ── ──── Common helpers ──────────────────────────────────────────────────

TEMP_DIR = os.path.join(os.path.expanduser("~/.hermes"), "temp", "echo")
THRESHOLD = 75
POLL_INTERVAL = 30  # watchdog loop interval (seconds)
SKIP_PATTERNS = ["cron", "subagent", "delegate", "kanban"]


def _get_hermes_home() -> str:
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return env
    return os.path.expanduser("~/.hermes")


def _triggered_at_path() -> str:
    return os.path.join(TEMP_DIR, "_triggered_at.json")


def _load_triggered_at() -> dict:
    try:
        p = _triggered_at_path()
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_triggered_at(data: dict) -> None:
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        with open(_triggered_at_path(), "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _is_disposable(session_id: str) -> bool:
    """Skip cron, subagent, delegate, kanban sessions."""
    if not session_id:
        return True
    sid_lower = session_id.lower()
    for p in SKIP_PATTERNS:
        if p in sid_lower:
            return True
    return False


def _is_process_alive(pid: int) -> bool:
    """Check if a PID is alive on Windows or Unix."""
    if sys.platform == "win32":
        import ctypes
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _load_existing_count(session_id: str, state_db: str) -> int:
    try:
        conn = sqlite3.connect(state_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND active = 1",
            (session_id,),
        )
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def _discover_profiles(hermes_home: str) -> list:
    """Scan ~/.hermes/profiles/ for active state.db files.

    Returns list of dicts: {name, state_db}
    """
    profiles = []
    profiles_dir = os.path.join(hermes_home, "profiles")
    if not os.path.isdir(profiles_dir):
        return profiles
    for entry in sorted(os.listdir(profiles_dir)):
        state_db = os.path.join(profiles_dir, entry, "state.db")
        if os.path.isfile(state_db):
            profiles.append({"name": entry, "state_db": state_db})
    # Save discovered profiles to temp for inspection
    profiles_file = os.path.join(TEMP_DIR, "_profiles.json")
    try:
        with open(profiles_file, "w") as f:
            json.dump(profiles, f, indent=2)
    except Exception:
        pass
    return profiles


# ── ──── One-shot recite-polling logic (original echo-poll.py) ──────────

def _run_recite_poll(session_id: str, unique_ts: str, hermes_home: str, profile: str = ""):
    """Phase 1-4: Poll for recite file, run compare, write verdict, cleanup."""
    recite_file = os.path.join(TEMP_DIR, f"echo-recite-{session_id}--{unique_ts}.md")
    verdict_file = os.path.join(TEMP_DIR, f"echo-verdict-{session_id}--{unique_ts}.md")
    compare_script = os.path.join(hermes_home, "skills", "echo-context-health", "scripts", "echo-compare.py")

    rec_poll_interval = 5
    rec_timeout = 120
    cleanup_wait = 60

    start = time.time()
    found = False
    while time.time() - start < rec_timeout:
        if os.path.exists(recite_file):
            found = True
            break
        time.sleep(rec_poll_interval)

    if not found:
        sys.exit(0)

    # Phase 2: Run comparison
    env = os.environ.copy()
    if profile:
        env["HERMES_PROFILE"] = profile
    try:
        result = subprocess.run(
            [sys.executable or "python", compare_script, recite_file],
            capture_output=True, text=True, timeout=30, env=env
        )
        lines = result.stdout.strip().split("\n")
        verdict = lines[0] if len(lines) > 0 else "skip"
        response = lines[1] if len(lines) > 1 else ""
        instruction = lines[2] if len(lines) > 2 else ""

        with open(verdict_file, "w") as f:
            f.write(f"{verdict}\n{response}\n{instruction}\n")
    except Exception:
        sys.exit(0)

    # Phase 3: Wait for agent to read verdict
    time.sleep(cleanup_wait)

    # Phase 4: Cleanup
    for pattern in [
        f"echo-instruct-{session_id}--{unique_ts}.md",
        f"echo-recite-{session_id}--{unique_ts}.md",
        f"echo-verdict-{session_id}--{unique_ts}.md",
        f"echo-compare-{session_id}--{unique_ts}.txt",
        f"echo-poll-{session_id}--{unique_ts}.log",
    ]:
        fpath = os.path.join(TEMP_DIR, pattern)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass
    sys.exit(0)


# ── ──── Fire ECHO for a specific session ───────────────────────────────

def _fire_echo(session_id: str, hermes_home: str, profile: str = ""):
    """Write instruct file, launch one-shot poll script, self-prompt agent.

    Uses 'hermes -p <profile>' for profile sessions (no env var trickery).
    """
    unique_suffix = str(int(time.time() * 1000))
    instruct_file = os.path.join(TEMP_DIR, f"echo-instruct-{session_id}--{unique_suffix}.md")
    instruct_basename = os.path.basename(instruct_file)

    # Write instruct file
    content = f"""# ECHO Context Health Check

## What to Do

1. Write the canary story from MEMORY ONLY to:
   `~/.hermes/temp/echo/echo-recite-{session_id}--{unique_suffix}.md`

2. After writing the recite file, poll every 10s (max 45s) for:
   `~/.hermes/temp/echo/echo-verdict-{session_id}--{unique_suffix}.md`

3. When the verdict file appears, read it. It has 3 lines:
   Line 1: verdict (an actual emoji)
   Line 2: what to print in chat
   Line 3: what to do next — follow it exactly

4. If the verdict file does not appear after 45s, ignore and resume your task.

## Rules

- Do NOT look up story files. Recite from memory only.
- Do NOT narrate steps in chat. Only print Line 2 from the verdict file.
"""
    try:
        with open(instruct_file, "w", encoding="utf-8") as f:
            f.write(content)
    except IOError as e:
        print(f"[ECHO] Warning - could not write instruction file: {e}")
        return

    # Launch one-shot recite-poll script
    poll_script = os.path.join(hermes_home, "plugins", "echo-context-health", "echo-poll.py")
    if os.path.exists(poll_script) and unique_suffix:
        args = [
            sys.executable or "python", poll_script,
            "--recite-poll", session_id, unique_suffix, hermes_home
        ]
        if profile:
            args.append(profile)
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # Self-prompt — use -p flag for profile sessions
    if profile:
        hermes_cmd = (
            f'hermes -p {profile} chat -r {session_id} -Q -q '
            f'"System: ECHO — read ~/.hermes/temp/echo/{instruct_basename}"'
        )
    else:
        hermes_cmd = (
            f'hermes chat -r {session_id} -Q -q '
            f'"System: ECHO — read ~/.hermes/temp/echo/{instruct_basename}"'
        )
    try:
        subprocess.run(
            hermes_cmd, shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        )
    except Exception as e:
        print(f"[ECHO] Warning - could not trigger health check: {e}")


# ── ──── Check sessions in one state.db ──────────────────────────────────

def _check_db_sessions(state_db: str, triggered_at: dict, hermes_home: str, profile: str = ""):
    """Check all active sessions in a state.db and fire ECHO at threshold.

    Args:
        state_db: Path to the SQLite database
        triggered_at: Shared _triggered_at dict (modified in place)
        hermes_home: Path to the Hermes home for finding poll/compare scripts
        profile: Profile name (empty string = default agent)
    """
    try:
        conn = sqlite3.connect(state_db)
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT m.session_id
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.active = 1
              AND (s.archived IS NULL OR s.archived = 0)
              AND (s.end_reason IS NULL OR s.end_reason != 'compression')
        """)
        session_ids = [r[0] for r in cur.fetchall()]
        conn.close()
    except Exception:
        return

    for sid in session_ids:
        if _is_disposable(sid):
            continue

        total = _load_existing_count(sid, state_db)
        if total < 0:
            continue

        # First encounter — initialize at threshold boundary
        if sid not in triggered_at:
            boundary = total - (total % THRESHOLD) if THRESHOLD > 0 else total
            triggered_at[sid] = boundary
            _save_triggered_at(triggered_at)

        last_triggered = triggered_at.get(sid, total)
        if total - last_triggered >= THRESHOLD:
            pending_flag = os.path.join(TEMP_DIR, f".trigger-pending-{sid}")
            if os.path.exists(pending_flag):
                continue

            try:
                open(pending_flag, "w").close()
            except Exception:
                pass

            triggered_at[sid] = total
            _save_triggered_at(triggered_at)

            _fire_echo(sid, hermes_home, profile)

            try:
                os.remove(pending_flag)
            except Exception:
                pass


# ── ──── Watchdog loop ──────────────────────────────────────────────────

def _run_watchdog(agent_name: str, parent_pid: int):
    """Main watchdog loop: every 30s poll all state.dbs, fire at threshold."""
    pid_file = os.path.join(TEMP_DIR, f".watchdog-{agent_name}.pid")
    hermes_home = _get_hermes_home()

    # Write PID file
    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    # Discover profiles on startup (one-time)
    default_db = hermes_home + "/state.db"
    profiles = _discover_profiles(hermes_home)

    while True:
        # Check if parent gateway is alive
        if not _is_process_alive(parent_pid):
            break  # Gateway died — self-terminate

        triggered_at = _load_triggered_at()

        # Check default agent's sessions
        if os.path.isfile(default_db):
            _check_db_sessions(default_db, triggered_at, hermes_home, profile="")

        # Check each profile's sessions
        for p in profiles:
            if os.path.isfile(p["state_db"]):
                _check_db_sessions(p["state_db"], triggered_at, hermes_home, profile=p["name"])

        _save_triggered_at(triggered_at)
        time.sleep(POLL_INTERVAL)

    # Cleanup PID file on exit
    try:
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                stored_pid = int(f.read().strip())
            if stored_pid == os.getpid():
                os.remove(pid_file)
    except Exception:
        pass
    sys.exit(0)


# ── ──── Entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Watchdog: echo-poll.py --agent <name> --parent-pid <pid>")
        print("  Recite:   echo-poll.py --recite-poll <sid> <ts> <hermes_home> [profile]")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "--recite-poll" and len(sys.argv) >= 5:
        sid = sys.argv[2]
        ts = sys.argv[3]
        home = sys.argv[4]
        prof = sys.argv[5] if len(sys.argv) > 5 else ""
        _run_recite_poll(sid, ts, home, prof)

    elif mode == "--agent":
        agent = ""
        parent_pid = 0
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--parent-pid" and i + 1 < len(sys.argv):
                parent_pid = int(sys.argv[i + 1])
                i += 2
            else:
                if not agent:
                    agent = sys.argv[i]
                i += 1

        if not agent or parent_pid == 0:
            print("Error: --agent and --parent-pid required for watchdog mode")
            sys.exit(1)
        _run_watchdog(agent, parent_pid)

    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
