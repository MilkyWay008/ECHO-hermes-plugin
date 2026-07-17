---
name: echo-install
description: "Install ECHO (Effective Context Health Optimization) — monitors agent context health and triggers soft resets when degradation is detected."
---

# ECHO INSTALL v2.3

## Architecture Overview

ECHO v2.3 uses a **single watchdog** launched by the default gateway on startup. The watchdog discovers **all Hermes profiles** automatically and monitors their sessions from one process.

```
Default gateway starts
  → HookRegistry fires gateway:startup
  → handler.py launches echo-poll.py in watchdog mode
      ↓
Watchdog (echo-poll.py):
  → Writes .watchdog-default.pid (prevents duplicates)
  → Profiles discovery (one-time): scans ~/.hermes/profiles/*/state.db
  → Writes profiles.json to temp/echo/ for inspection
  → Every 30s:
       → Polls default state.db + all discovered profile state.dbs
       → For any session crossing threshold:
           → Write instruct file
           → Launch echo-poll.py --recite-poll (one-shot)
           → Self-prompt via: hermes -p <profile> chat -r {sid} -Q -q "..."
  → If parent gateway PID dies → self-terminates
```

**Config discovery** — the skill file and config.json are shared from the default profile. No per-profile config needed. The instruct file is self-contained (the agent doesn't need the skill loaded — it reads the file and follows instructions).

## What You're Installing

| Component | Where it goes | Purpose |
|-----------|---------------|---------|
| `plugin/echo-context-health/` | `~/.hermes/plugins/echo-context-health/` | **Dual-mode watchdog.** Watchdog (default): long-running loop, multi-profile, fires at threshold. One-shot (`--recite-poll`): for each ECHO fire, polls recite file, runs compare, writes verdict. |
| `hooks/echo-context-health/` | `~/.hermes/hooks/echo-context-health/` | HookRegistry adapter — launches the watchdog on `gateway:startup`. Has simple PID guard to prevent duplicates on crash-restart. |
| `skill/` | `~/.hermes/skills/echo-context-health/` | Protocol, config, compare script, canary story reference, documentation. |

### No Per-Profile Install Needed

The watchdog automatically discovers all profiles with `state.db` files:
- gf-helen, itgirl-helen, projectmanager-helen, sysadmin-helen, etc.
- Each is monitored independently — sessions from any profile trigger ECHO when threshold is crossed.
- Self-prompt routes to the correct profile via `hermes -p <profile> chat -r {sid} -Q -q "..."`.

### SOUL.md — Inject the Canary Story

For ECHO to work, the agent must have the canary story in its persistent context. SOUL.md is the agent's identity file — read at session start and refreshed during soft resets.

The canary story is appended **after** a `---` separator, below the persona section. This keeps the story available for recitation without affecting the agent's persona or behavior:

```bash
# First, add a separator after your persona
echo -e "\n--- ECHO Canary Story ---" >> ~/.hermes/SOUL.md
# Then append the story
cat skill/references/echo-canary-story.md >> ~/.hermes/SOUL.md
```

The result looks like:

```
[Your persona, rules, and identity...]

--- ECHO Canary Story ---
[The Great Intergalactic Submarine Heist]
```

The `---` separator tells the agent: "below this line is reference material, not behavioral instruction." The story sits there as a calibration tool — the agent stays in its persona, just with a silly story in its back pocket that it can recite on demand during health checks.

> **If you use profile agents (e.g., gf-helen, itgirl-helen):** Each profile has its own SOUL.md at `~/.hermes/profiles/<name>/SOUL.md`. You must repeat the injection for **every** profile that runs a gateway, so each profile's agent has the canary story in its persistent context. The watchdog monitors all profiles' sessions, but the **agent** in each profile needs the story in its own SOUL.md to recite it during ECHO checks.

### Initial Cleanup

If upgrading from a previous version, clean stale temp files that may block the watchdog:

```bash
rm -f ~/.hermes/temp/echo/.trigger-pending-*
rm -f ~/.hermes/temp/echo/echo-instruct-*
rm -f ~/.hermes/temp/echo/echo-recite-*
rm -f ~/.hermes/temp/echo/echo-verdict-*
rm -f ~/.hermes/temp/echo/echo-compare-*
rm -f ~/.hermes/temp/echo/echo-poll-*
```

## Prerequisites

- Hermes Agent installed and running
- `hermes` CLI in PATH (verify: `hermes --version`)
- Default gateway running (Scheduled Task or direct)

## Install

```bash
# 1. Copy plugin (watchdog + one-shot poller)
cp -r plugin/echo-context-health ~/.hermes/plugins/echo-context-health

# 2. Copy hook (launches watchdog on gateway:startup)
cp -r hooks/echo-context-health ~/.hermes/hooks/echo-context-health

# 3. Copy skill (protocol, config, compare script, references)
cp -r skill ~/.hermes/skills/echo-context-health

# 4. Enable plugin
hermes plugins enable echo-context-health

# 5. Restart gateway
hermes gateway restart
```

**Note:** The HookRegistry adapter does NOT need separate "enable" — the gateway auto-discovers all hooks in `~/.hermes/hooks/` on startup.

## Verification

After gateway restart, check:
```bash
# Watchdog PID file exists and process is alive
cat ~/.hermes/temp/echo/.watchdog-default.pid

# Discovered profiles
cat ~/.hermes/temp/echo/_profiles.json

# Watchdog is monitoring sessions
# After 75+ messages in any session, ECHO fires automatically
```

When ECHO fires:
1. Watchdog writes `echo-instruct-{sid}--{ts}.md`
2. One-shot poll launches: polls every 5s for recite file (120s timeout)
3. Agent receives self-prompt → reads instruct → writes story from memory
4. One-shot detects recite file → runs `echo-compare.py`
5. Writes `echo-verdict-{sid}--{ts}.md`
6. Agent polls every 10s (max 45s) → finds verdict → follows line 3 (soft reset or continue)
7. After 60s, one-shot cleans up all temp files

> **Note:** If a one-shot poll process crashes before completing cleanup (e.g., gateway crash), verdict files may remain. These are harmless but can be cleaned with `rm -f ~/.hermes/temp/echo/echo-verdict-*`.

You see one of:
- `🟢 Context Health` — all good, continue
- `🟡 Context Health; 1 sec, let me refocus very quick.` — soft reset
- `🔴 Context Health; 1 sec, let me refocus very quick.` — soft reset

## Profile Coverage

The single watchdog covers ALL profiles automatically. No separate install needed per profile. The watchdog:

1. Scans `~/.hermes/profiles/*/state.db` on startup
2. Polls each profile's state.db every 30s
3. Self-prompts via `hermes -p <profile> chat -r {sid} -Q -q "..."` to target the correct profile's session
4. Shares `echo-poll.py --recite-poll` (one-shot) and `echo-compare.py` across all profiles

Profiles without `state.db` (e.g., no gateway ever ran for that profile) are simply skipped.

## Configuration

Edit `~/.hermes/skills/echo-context-health/config.json`:

```json
{
  "message_threshold": 75
}
```

Lower (30-50) for tight-focus tasks. Higher (100-150) for long research sessions.

## Uninstall

```bash
# Disable plugin
hermes plugins disable echo-context-health

# Remove plugin (watchdog)
rm -rf ~/.hermes/plugins/echo-context-health

# Remove hook (handler)
rm -rf ~/.hermes/hooks/echo-context-health

# Remove skill
rm -rf ~/.hermes/skills/echo-context-health

# Remove temp files
rm -rf ~/.hermes/temp/echo/

# Restart gateway
hermes gateway restart
```

## Files to Not Modify

| File | Why |
|------|-----|
| `plugins/echo-context-health/echo-poll.py` | Dual-mode watchdog — only modify if debugging |
| `plugins/echo-context-health/__init__.py` | Plugin stub — no hooks registered, all logic in echo-poll.py |
| `plugins/echo-context-health/plugin.yaml` | Plugin manifest |
| `hooks/echo-context-health/handler.py` | Hook handler — launches watchdog on gateway:startup |
| `hooks/echo-context-health/HOOK.yaml` | Hook manifest — must declare `gateway:startup` |
| `skill/scripts/echo-compare.py` | Verdict script — agent must never override its output |
| `skill/references/echo-canary-story.md` | Ground truth — agent must NOT read this during ECHO triggers |
