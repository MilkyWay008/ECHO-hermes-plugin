"""
ECHO Stress Test v2 — sends sequential search queries to a Hermes session
until ECHO fires. Each query generates 1-3 tool calls.

Usage: python echo-stress-v2.py <session_id> [message_threshold]
"""
import subprocess
import sys
import os
import json
import time

SESSION_ID = sys.argv[1] if len(sys.argv) > 1 else None
THRESHOLD = int(sys.argv[2]) if len(sys.argv) > 2 else 75

if not SESSION_ID:
    print("Usage: python echo-stress-v2.py <session_id> [threshold]")
    sys.exit(1)

ECHO_TEMP = os.path.join(os.path.expanduser("~/.hermes"), "temp", "echo")
TRIGGER_FILE = os.path.join(ECHO_TEMP, "_triggered_at.json")
env = os.environ.copy()

PATTERNS = [
    "*.py", "*.js", "*.ts", "*.json", "*.yaml", "*.yml",
    "*.txt", "*.md", "*.cfg", "*.ini", "*.conf", "*.toml",
    "*.xml", "*.csv", "*.log", "*.bat", "*.cmd", "*.ps1",
    "*.sh", "*.env", "*.sql", "*.html", "*.css", "*.vue",
    "*.svelte", "*.go", "*.rs", "*.rb", "*.php", "*.java",
    "*.kt", "*.swift", "*.c", "*.h", "*.cpp", "*.hpp",
    "*.dart", "*.lua", "*.scala", "*.zig", "Makefile"
]

def get_trigger_state():
    try:
        with open(TRIGGER_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def get_current_asst_count():
    """Query state.db for current assistant message count."""
    state_db = os.path.join(os.path.expanduser("~/.hermes"), "state.db")
    # Also check profile path
    profile = os.environ.get("HERMES_PROFILE", "").strip()
    if profile:
        state_db = os.path.join(os.path.expanduser("~/.hermes"), "profiles", profile, "state.db")
    try:
        import sqlite3
        conn = sqlite3.connect(state_db)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND active = 1",
            (SESSION_ID,),
        )
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0

# Record initial state — use DB count, not _triggered_at (may be uninitialized)
initial_asst = get_current_asst_count()
initial_ta = get_trigger_state().get(SESSION_ID, initial_asst)
print(f"Session: {SESSION_ID}")
print(f"Starting assistant msgs: {initial_asst}")
print(f"Starting _triggered_at: {initial_ta}")
print(f"Threshold: {THRESHOLD}")
print(f"Patterns to send: {len(PATTERNS)}")
print()

queries_sent = 0
for i, pattern in enumerate(PATTERNS):
    queries_sent += 1
    msg = f"search target=files pattern={pattern}"
    
    start = time.time()
    print(f"[{i+1}/{len(PATTERNS)}] Sending: {msg} ...", end=" ", flush=True)
    
    try:
        result = subprocess.run(
            ["hermes", "chat", "-r", SESSION_ID, "-Q", "-q", msg],
            capture_output=True, text=True, timeout=120, env=env
        )
        elapsed = time.time() - start
        output = result.stdout.strip().split("\n")[-1][:60] if result.stdout.strip() else ""
        print(f"done in {elapsed:.0f}s — {output}")
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"timed out at {elapsed:.0f}s (message still queued)")
    except Exception as e:
        print(f"error: {e}")
    
    # Check if ECHO fired — _triggered_at must increase by at least threshold
    current_state = get_trigger_state()
    current_ta = current_state.get(SESSION_ID, initial_ta)
    fired = current_ta >= initial_ta + THRESHOLD
    if fired:
        print(f"\n>>> ECHO FIRED! _triggered_at: {initial_ta} → {current_ta} <<<")
        break
    
    # Polite delay between queries
    time.sleep(0.5)

print(f"Test complete. Queries sent: {queries_sent}")
print(f"Final _triggered_at: {get_trigger_state().get(SESSION_ID, '?')}")

# Self-report to the agent — unique signature so agent knows it's from the script, not the user
_target_session = os.environ.get("HERMES_TARGET_SESSION", "")
if _target_session:
    try:
        report = f"ECHO-STRESS-REPORT: Session {SESSION_ID} — {queries_sent} queries sent. ECHO {'FIRED' if fired else 'NOT FIRED'}. Final _triggered_at: {current_ta if fired else 'unchanged'}."
        subprocess.run(
            ["hermes", "chat", "-r", _target_session, "-Q", "-q", f"⚙️ {report}"],
            capture_output=True, timeout=30, env=env
        )
    except Exception:
        pass
