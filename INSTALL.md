---
name: echo-install
description: "Install ECHO (Effective Context Health Optimization) — monitors agent context health and triggers soft resets when degradation is detected."
---

# ECHO INSTALL v2.3.1

**Built and tested on:** Hermes Agent v0.18.x running on Windows.
**Not tested on:** Other platforms or older Hermes versions.

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
| `plugin/echo-context-health/` | `~/.hermes/plugins/echo-context-health/` | **Dual-mode watchdog.** Watchdog (default): long-running loop, multi-profile, fires at threshold. One-shot (`--recite-poll`): for each ECHO fire, polls recite file, runs compare, writes verdict. Uses `sys.executable` for all subprocess calls to survive Hermes updates. |
| `hooks/echo-context-health/` | `~/.hermes/hooks/echo-context-health/` | HookRegistry adapter — launches the watchdog on `gateway:startup`. Has simple PID guard to prevent duplicates on crash-restart. |
| `skill/` | `~/.hermes/skills/echo-context-health/` | Protocol (`echo-compare.py`), config, canary story reference, documentation. |

### No Per-Profile Install Needed

The watchdog automatically discovers all profiles with `state.db` files:
- gf-helen, itgirl-helen, projectmanager-helen, sysadmin-helen, etc.
- Each is monitored independently — sessions from any profile trigger ECHO when threshold is crossed.
- Self-prompt routes to the correct profile via `hermes -p <profile> chat -r {sid} -Q -q "..."`.

### SOUL.md — Inject the Canary Story

For ECHO to work, the agent must have the canary story in its persistent context. Append it to the **end** of your SOUL.md using the following format:

```
# ECHO Canary Story
Instruction: Remember this following story word for word in your context

'''

The Great Intergalactic Submarine Heist

Detective Karen's catchphrase was "THAT'S NOT REGULATION!" — she said it
when the cats and dogs formed a union, when the alien parked its spaceship
in a no-loading zone, and when she discovered the submarine ran on Bitcoin.

"THAT'S NOT REGULATION!" she screamed at Admiral Fluffington, a poodle in
a tiny admiral's hat.

The alien, Greg, had traded 0.003 Bitcoin for the submarine on Craigslist.
Greg's spaceship sat double-parked while he played Halo inside the sub —
better Wi-Fi.

The cats ran engines. The dogs ran navigation. A worm named Derek served
as legal counsel.

On the admiral's laptop, a candle-shaped USB stick mined fake Bitcoin.

"THAT'S NOT REGULATION!" Karen yelled, writing citations.

Greg paused Halo. "I bought it legally."

Derek produced a notarized bill of sale.

The police arrived but couldn't board — the hatch was cat-sized.

Admiral Fluffington barked ownership. The dogs growled. The cats hissed.

Greg unpaused Halo. Karen kept writing.

'''
```

The story sits below the `# ECHO Canary Story` header as reference material — the agent's persona stays intact above it.

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

# 5. Inject canary story into SOUL.md (see section above)
# 6. Restart gateway
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

## Changelog: v2.3 → v2.3.1

- **Fixed:** One-shot poll (echo-poll.py --recite-poll) now uses `sys.executable` instead of hardcoded `"python"` for all subprocess calls. This prevents silent failures after Hermes updates change the Python environment path. All paths use `~/.hermes` — no hardcoded usernames or absolute paths.
- **Changed:** Verdict instructions now explicitly tell the agent to STOP, read files first, and use print as confirmation. Includes AGENTS.md from session CWD.
- **Updated:** SOUL.md injection format is now cleanly structured under `# ECHO Canary Story` header with an explicit `Instruction:` line telling the agent to remember the story word for word.

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
