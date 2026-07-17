---
name: echo-context-health
description: "ECHO (Effective Context Health Optimization) — monitors context health via a canary story recitation test, using a watchdog-based polling architecture that covers ALL session types (CLI/TUI and gateway) without requiring any PluginManager hooks."
version: 2.5.0
author: Skywalker
tags: [context, health, monitoring, canary, echo, watchdog, polling]
---

# ECHO — Effective Context Health Optimization (v2.3 Watchdog)

A self-contained, per-session context health monitoring and remediation system for Hermes Agent. Detects context degradation in long-running sessions and triggers a soft reset — without cron, webhooks, or external dependencies.

**Key architectural change in v2.3:** Instead of relying on Hermes' two hook systems (PluginManager + HookRegistry) — neither of which covers all session types — ECHO v2.3 uses a **standalone watchdog** that polls `state.db` directly every 30s. This works for CLI, TUI, API server, Telegram, Discord, and every other session type without modifying a single line of Hermes core code.

## How It Works

### Agent's View (unchanged from v2.2)

When the watchdog fires, a self-prompt arrives as a normal message:

```
System: ECHO — read ~/.hermes/temp/echo/echo-instruct-{session_id}--{unique_suffix}.md
```

### Agent Protocol (from the instruct file)

```
1. Write the canary story from MEMORY ONLY to:
   ~/.hermes/temp/echo/echo-recite-{session_id}--{unique_suffix}.md

2. After writing, poll every 10s (max 45s) for:
   ~/.hermes/temp/echo/echo-verdict-{session_id}--{unique_suffix}.md

3. When the verdict file appears, read it — it has 3 lines:
   Line 1: verdict emoji
   Line 2: what to print in chat (print ONLY this line)
   Line 3: what to do next (follow it exactly)

4. If the verdict file does not appear after 45s, ignore and resume.
```

### Rules

- **Do NOT** look up story reference files. Recite from memory only.
- **Do NOT** narrate steps in chat. Only print Line 2 from the verdict file.
- **Do NOT** self-verify. The comparison script is the ONLY valid verdict.
- The instruct file is self-contained — you do not need to load this skill during an ECHO trigger.

## Architecture (v2.3 Watchdog)

### Why the Watchdog Replaced Plugins + Hooks

Hermes Agent has **two separate hook systems**, and neither one reliably fires for all session types:

| Hook System | CLI/TUI | API Server | Telegram/Discord |
|-------------|---------|------------|------------------|
| PluginManager (`plugins/`) | ✅ Works | ❌ Gateway never initializes it | ❌ Same |
| HookRegistry (`hooks/`) via `agent:end` | N/A | ❌ API server bypasses dispatch | ✅ Works |

The v2.2 dual-hook approach (both PluginManager and HookRegistry) failed because:
1. The gateway process never calls `PluginManager.discover_and_load()` — `invoke_hook("post_llm_call", ...)` finds an empty PluginManager with zero callbacks
2. The HookRegistry `agent:end` event is only emitted in `_dispatch_agent()` — the API server adapter bypasses this by calling `agent.run_conversation()` directly

**v2.3 sidesteps the entire problem** by polling the universal source of truth: `state.db`.

### The Watchdog Flow

```
Gateway starts
  → HookRegistry fires gateway:startup event
  → handler.py receives it (SOLE remaining hook)
  → Checks per-agent PID file (.watchdog-{agent}.pid)
     → If alive → skip (already running)
     → If dead/missing → launch echo-poll.py --agent {name} --parent-pid {gateway_pid}

echo-poll.py (watchdog mode):
  Writes .watchdog-{agent}.pid
  Every 30s:
    → Is parent gateway PID alive? NO → cleanup PID file → EXIT
    → Query state.db for ALL active sessions
    → For each session NOT in skip-list:
        → Check _triggered_at boundary
        → If diff >= 75: fire ECHO to that session
            → Write instruct file (unique timestamp)
            → Launch echo-poll.py --recite-poll (one-shot mode)
            → Self-prompt agent via hermes chat -r
            → Update _triggered_at[sid] = total
    → Sleep 30s

One-shot mode (echo-poll.py --recite-poll, launched per-ECHO-fire):
  Phase 1: Poll every 5s for recite file (120s timeout)
  Phase 2: Run echo-compare.py → writes verdict file
  Phase 3: Wait 60s (agent reads verdict)
  Phase 4: Delete all temp files
```

### Per-Agent Isolation

Each gateway instance manages its own watchdog via a PID file named by agent:

| Agent | PID File | state.db |
|-------|----------|----------|
| `default` | `.watchdog-default.pid` | `$HERMES_HOME/state.db` |
| `gf-helen` | `.watchdog-gf-helen.pid` | `$HERMES_HOME/profiles/gf-helen/state.db` |

- Default gateway crash & restart: checks `.watchdog-default.pid` — old PID dead → starts fresh
- gf-helen gateway starts: checks `.watchdog-gf-helen.pid` — doesn't exist → starts its own
- Default watchdog still running, gf-helen starts: checks `.watchdog-gf-helen.pid` — never confused by default's PID file

### How Duplicate Fires Are Prevented

1. `_triggered_at` is updated BEFORE self-prompting — next 30s poll sees diff=0
2. `.trigger-pending-{sid}` file blocks cross-process duplicates (same as v2.2)
3. Per-agent PID files prevent duplicate watchdog instances for the same agent (v2.3 addition)

### The Counting Logic (unchanged)

Counts **all** messages (user + assistant + tool) — every message eats context:

```
Total messages: 202, threshold 75
  boundary = 202 - (202 % 75) = 150
  _triggered_at initialized at 150 (last threshold boundary)
  diff = 202 - 150 = 52 < 75
  Need 23 more messages → fire at 225
```

- **New session (0 msgs):** fires at 75, 150, 225...
- **Existing session (180 msgs):** boundary = 150, need 45 more
- **Existing session (202 msgs):** boundary = 150, need 23 more

### File-Based Communication (Anti-Cheating)

| File | Who Writes | Who Reads | Purpose |
|------|-----------|-----------|---------|
| `echo-instruct-{sid}--{ts}.md` | Watchdog | Agent | Tells agent the recite path and verdict file to expect |
| `echo-recite-{sid}--{ts}.md` | Agent | One-shot poller | The recitation — agent's ONLY job |
| `echo-verdict-{sid}--{ts}.md` | One-shot poller | Agent | Contains 3 lines: verdict, chat response, next action |
| `.watchdog-{agent}.pid` | Watchdog | Handler on next startup | Prevents duplicate watchdog for the same agent |
| `.trigger-pending-{sid}` | Watchdog | Watchdog (next cycle) | Prevents duplicate fires for same session |
| `_triggered_at.json` | Watchdog | Watchdog (persistent) | Per-session threshold tracking |

**Agent has ONE job:** write the recite file. The watchdog and one-shot poller handle everything else.

### Profile Awareness

The watchdog auto-detects profile gateways via `HERMES_PROFILE` env var:
- **Default profile:** reads `$HERMES_HOME/state.db`
- **Profile agent (gf-helen):** reads `$HERMES_HOME/profiles/{name}/state.db`
- Self-prompt subprocess forwards `HERMES_PROFILE` so the CLI child connects to the correct session DB

## What Changed From v2.2

| Component | v2.2 (Dual-Hook) | v2.3 (Watchdog) |
|-----------|------------------|-----------------|
| `plugins/__init__.py` | Full PluginManager hooks (`post_llm_call`, `pre_tool_call`) | **Empty stub** — all logic moved to watchdog |
| `plugins/plugin.yaml` | `kind: hook` (irrelevant) | Basic manifest, no kind |
| `plugins/echo-poll.py` | One-shot: poll one recite file, compare, cleanup | **Dual-mode**: watchdog loop (30s) + one-shot (`--recite-poll`) |
| `hooks/HOOK.yaml` | `events: [agent:end]` (never fired for API sessions) | `events: [gateway:startup]` — fires on all gateway starts |
| `hooks/handler.py` | Handle `agent:end` → count msgs | Handle `gateway:startup` → launch watchdog with parent PID |
| Coverage type | Two separate systems for different session types | **One system** covering all session types via state.db |

## Files

| Path | Purpose |
|------|---------|
| `plugins/echo-context-health/__init__.py` | **Stub** — no hooks. Watchdog handles everything. |
| `plugins/echo-context-health/echo-poll.py` | **Dual-mode watchdog.** Watchdog mode (default): 30s polling loop. One-shot mode (`--recite-poll`): poll recite file, run compare, verdict, cleanup. |
| `plugins/echo-context-health/plugin.yaml` | Basic manifest. |
| `hooks/echo-context-health/HOOK.yaml` | `events: [gateway:startup]` — launches watchdog on gateway start. |
| `hooks/echo-context-health/handler.py` | Checks per-agent PID file, launches watchdog with parent PID tracking. |
| `skills/echo-context-health/SKILL.md` | This file. |
| `skills/echo-context-health/skill-info.md` | Reference documentation (architecture, calibration, debugging checklist). |
| `skills/echo-context-health/config.json` | `message_threshold` (default 75). |
| `skills/echo-context-health/scripts/echo-compare.py` | String comparator — 3-line output (verdict, response, instruction). |
| `skills/echo-context-health/references/echo-canary-story.md` | Ground-truth canary story with tier annotations. |

## Configuration

```json
{
  "message_threshold": 75
}
```

Higher (100-150) for long research sessions. Lower (30-50) for tight focus tasks.

## Verdict Meanings

| Line 1 | Meaning | Agent prints | Agent does next |
|--------|---------|-------------|----------------|
| 🟢 | Context healthy | `🟢 Context Health` | Continue silently |
| 🟡 | Minor degradation | `🟡 Context Health; 1 sec...` | Soft reset: re-read SOUL.md, MEMORY.md, USER.md |
| 🔴 | Serious degradation | `🔴 Context Health; 1 sec...` | Soft reset: re-read SOUL.md, MEMORY.md, USER.md |

## Deployment

### Default Profile

```bash
# 1. Plugin (echo-poll.py watchdog — required for one-shot mode)
cp -r plugin/echo-context-health ~/.hermes/plugins/echo-context-health

# 2. Hook adapter (launches watchdog on gateway:startup)
cp -r hooks/echo-context-health ~/.hermes/hooks/echo-context-health

# 3. Skill
cp -r skill ~/.hermes/skills/echo-context-health

# 4. Restart gateway — HookRegistry handler will auto-launch watchdog
hermes gateway restart
```

**The plugin does NOT need `hermes plugins enable`** — the watchdog is launched by the HookRegistry handler, not by the PluginManager. The plugin files (`__init__.py`, `plugin.yaml`) exist primarily for the `echo-poll.py` script location. If `hermes plugins list` shows it as disabled, that's fine.

### Profile Agent (e.g., gf-helen)

```bash
PROFILE=gf-helen
BASE=~/.hermes/profiles/$PROFILE

# 1. Plugin
cp -r plugin/echo-context-health $BASE/plugins/echo-context-health

# 2. Hook adapter (each profile has its own hooks/ directory)
mkdir -p $BASE/hooks
cp -r hooks/echo-context-health $BASE/hooks/echo-context-health

# 3. Skill
cp -r skill $BASE/skills/echo-context-health

# 4. Append canary story to $BASE/SOUL.md
cat skill/references/echo-canary-story.md >> $BASE/SOUL.md

# 5. Restart profile gateway
HERMES_PROFILE=$PROFILE hermes gateway restart
```

### Verification

1. After restart, check `.watchdog-{agent}.pid` exists in `~/.hermes/temp/echo/`
2. Send messages to any session through any interface (TUI, WebUI, API)
3. After `message_threshold` messages, the watchdog fires ECHO
4. The instruction file appears in `~/.hermes/temp/echo/`

## Known Limitations

### Watchdog Requires Gateway Restart

The watchdog is launched by the HookRegistry handler on `gateway:startup`. If the gateway is not restarted after installing the HookRegistry adapter, the watchdog will not run.

In a Scheduled Task gateway setup, killing the gateway process and starting it via `hermes gateway start` is sufficient — the Task Scheduler or `gateway start` command re-launches the process with the new handler.py loaded.

### No `kind: hook` in plugin.yaml

Setting `kind: hook` in plugin.yaml does **nothing**. There is no `"hook"` kind in Hermes — the default is `"standalone"`. Setting it logs a warning and falls back to standalone. The ECHO v2.3 plugin files no longer set this field.

## Related Skills

| Skill | Why |
|-------|-----|
| `hermes-hook-plugins` | Plugin development details. Contains the two-hook-system discovery and watchdog pattern reference. |
| `subagent-first` | Keeps main-agent context lean — fewer ECHO triggers by reducing per-turn message count. |
| `script-first-workflows` | Collapses 3+ tool calls into 1 — pushes the ECHO threshold further out. |

---

*ECHO v2.3 — watchdog-based, state.db-polling, zero-hook context health monitoring*
