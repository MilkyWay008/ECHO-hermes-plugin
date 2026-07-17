---
name: echo-context-health-info
description: "Reference documentation for ECHO — architecture, deployment, empirical calibration, and debugging guide. Not loaded during ECHO triggers."
version: 2.2.0
author: Skywalker
tags: [echo, reference, architecture, debugging, deployment]
---

# ECHO Context Health — Reference

> **This file is NOT loaded during ECHO triggers.** It exists for human reading and troubleshooting sessions. The trigger protocol lives in `SKILL.md`.

## Overview (How It Works)

1. **Monitoring**: The ECHO plugin tracks LLM responses per session via `post_llm_call` and `pre_tool_call` hooks
2. **Threshold**: After N assistant messages (configurable, default: 75), a health check fires
3. **Self-Prompt**: The plugin spawns `hermes chat -r {session_id} -Q -q "ECHO"` — guarantees mid-task interruption
4. **Canary Test**: The agent recites the story from memory → writes to file → comparison script scores it
5. **Verdict**: The script returns 🟢 (healthy), 🟡 (minor), or 🔴 (serious)
6. **Recovery**: If degradation detected, soft reset protocol restores coherence

## Memorability Tiers (used by echo-compare.py for scoring)

| Tier | Icon | Details |
|------|------|---------|
| Once-Mentioned | 🟡 | Derek the worm, candle-shaped USB, 0.003 Bitcoin, Craigslist, cat-sized hatch, tiny admiral's hat, notarized bill of sale, police couldn't board |
| Medium-Frequency | 🔴 | Admiral Fluffington, Greg, Halo, Bitcoin, spaceship, laptop, submarine |
| Repetitive Core | 🔴🔴 | "THAT'S NOT REGULATION!", cats, dogs |

## Dual-Hook Architecture

ECHO v2 registers **two hooks** for maximum coverage:

| Hook | When It Fires | Purpose |
|------|--------------|---------|
| `pre_tool_call` | **Before each tool call** within a turn | Catches threshold crossing MID-TURN, spawns self-prompt that agent sees on next LLM invocation |
| `post_llm_call` | After each complete LLM response turn | Catches any threshold crossed by messages from subprocesses or between turns |

**Why both?** A single long task can generate 20+ assistant messages (each tool call cycle = 1 assistant message). With only `post_llm_call`, ECHO waits for the entire turn to complete before checking. With `pre_tool_call`, it fires at ~tool call N, and the agent reads the ECHO prompt on the very next LLM invocation — just one tool call later.

**Duplicate protection:** Both hooks share `_check_and_trigger()` which uses:
- `_trigger_in_progress` (in-memory set) — prevents concurrent duplicate triggers
- `_triggered_at` (persisted JSON) — prevents sequential duplicate triggers (first hook to cross threshold updates it, second sees diff=0)

## Session ID Compaction & `_triggered_at` Poisoning

Hermes may compact sessions, which **changes the session ID**. When this happens:

1. `_triggered_at.json` retains the **old** session ID with its last-triggered count
2. The **new** session has no entry in `_triggered_at`
3. `_triggered_at.get(new_sid, total)` returns `total` → diff = 0 → **ECHO never fires**
4. The old session ID's stale entry persists indefinitely, wasting the slot

**Fix**: On every check, initialize unknown sessions at their current count so the first healthy check sets a proper baseline.

## Files

| Path | Purpose |
|------|---------|
| `plugins/echo-context-health/` | Plugin source (hook registration, counter logic, self-prompt trigger) |
| `skills/echo-context-health/SKILL.md` | **Trigger protocol** — loaded when ECHO fires |
| `skills/echo-context-health/skill-info.md` | This file — reference documentation |
| `skills/echo-context-health/scripts/echo-compare.py` | **The only valid verdict source** — run it, don't guess |
| `skills/echo-context-health/references/echo-canary-story.md` | Ground-truth canary story for reference (do NOT read during ECHO) |
| `skills/echo-context-health/references/session-forensics.md` | SQLite session forensics queries for empirical degradation analysis |
| `skills/echo-context-health/references/plugin-debugging-case.md` | Full debugging case study — 6 failure modes, heartbeat technique |
| `skills/echo-context-health/config.json` | `message_threshold` (default 75) |

## Per-Profile Deployment

Components are deployed at different scopes:

| Component | Scope | Deploy action |
|-----------|-------|--------------|
| Plugin (`__init__.py` + `plugin.yaml`) | **Per-profile** | Copy to `profiles/<name>/plugins/echo-context-health/` |
| Skill (SKILL.md + config + scripts + references) | **Per-profile** | Copy to `profiles/<name>/skills/echo-context-health/` |
| SOUL.md (canary story) | **Per-profile** | Append to each profile's SOUL.md |
| Temp files (`~/.hermes/temp/echo/`) | **Cross-profile** | Single shared directory — session-ID-scoped filenames prevent collisions |

The temp path is cross-profile safe because every file includes the `session_id` in its name (`echo-instruct-{session_id}.md`, `echo-recite-{session_id}.md`). Different profiles have different session IDs → no collisions.

## Configuration

```json
{
  "message_threshold": 75,
  "temp_dir": "~/.hermes/temp/echo/"
}
```

## Plugin Debugging Checklist

When hooks don't fire, use this escalation:

1. **`hermes plugins list`** — confirm plugin shows as `enabled` (not just present)
2. **Write-in-register diagnostic** — add a file write at the top of `register()` to confirm the plugin loaded
3. **Check config.yaml** — `plugins.enabled` must contain the plugin name exactly
4. **Check profile isolation** — profile-based sessions use `profiles/<name>/plugins/`, not the global one
5. **Check compaction** — session ID may have changed; check `state.db` for the current ID
6. **Heartbeat** — add a write at the top of each hook callback (see `references/plugin-debugging-case.md`)

## Empirical Calibration

Data from real session-forensics analysis of two sessions — one SSH/WSL fallback case, one destructive-command case. Methodology in `references/session-forensics.md`.

### Case Study A: SSH/WSL Rule Forgetting

Session `20260610_030431_c23242` — the agent repeatedly forgot the "SSH only, not WSL" rule across 201 user+assistant-message session.

**Degradation Thresholds**:
- Assistant messages before first fallback: ~93
- Tool calls before first fallback: ~75
- After first correction: runway drops to ~19 messages
- After second correction: ~5 messages

The cycle does not reset cleanly after a reminder. Each correction buys shorter runway. The 75-message threshold is a **preventive** upper bound.

### Case Study B: Destructive-Command Cycle

Session `20260607_031323_7c0b15` — a 496-message session (229 tool calls) where the agent used the correct tool (SSH) but forgot what commands were safe on the remote machine.

**Message Counts Between Corrections**:
- Initial → "AGAIN" reprimand: 108 assistant messages
- "AGAIN" → "disk full": 119 assistant messages
- "disk full" → "maxed out again": 13 assistant messages

### Synthesized Findings

| Failure Mode | Healthy Runway (asst msgs) | After First Correction | Root Cause |
|-------------|-------------------------------|----------------------|------------|
| Wrong tool (WSL vs SSH) | ~93 | ~16 | Procedural rule decay |
| Wrong command (destructive via correct tool) | ~108-119 | ~13-26 | Safety-rule decay |
| **Combined recommendation** | **Check at ~75** | — | Catch before any cycle breaks |

## Related Skills

| Skill | Why |
|-------|-----|
| `subagent-first` | Keeps main-agent context lean — fewer ECHO triggers by reducing per-turn message count |
| `script-first-workflows` | Collapses 3+ tool calls into 1 — pushes the ECHO threshold further out |

## Reference Files

| File | What it covers |
|------|---------------|
| `references/echo-canary-story.md` | The Great Intergalactic Submarine Heist with tier annotations |
| `references/session-forensics.md` | Empirical degradation data from real sessions |
| `references/plugin-debugging-case.md` | Full debugging journey — heartbeat technique, 6 failure modes, architecture lessons |

---

*ECHO v2.2 — Effective Context Health Optimization — Reference Documentation*
