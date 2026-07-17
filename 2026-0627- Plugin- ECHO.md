# ECHO v2 — Effective Context Health Optimization (v2.3 Production Final)

**Date:** 2026-07-02
**Status:** Production-ready
**Author:** Ringo/MilkyWay008

---

## 1. What Is ECHO

ECHO (Effective Context Health Optimization) is a **self-contained, per-session context health monitoring and remediation system** for Hermes Agent. It detects context degradation in long-running sessions and triggers soft resets to restore agent coherence — without cron, webhooks, Discord, or any external dependencies.

**Core Insight:** The very mechanism that causes degradation — older context fading from clear → vague → gone — is used as the detection sensor. A silly story with details at different repetition levels is read at session start. When once-mentioned details disappear from the agent's recitation, it signals early degradation (🟡). When medium-frequency details disappear, it signals serious degradation (🔴).

### Core Innovation: The Canary Story

A calibrated absurdist story ("The Great Intergalactic Submarine Heist") with details at four memorability tiers acts as a graduated sensor. When the agent recites the story from memory, which details are missing reveals exactly how degraded the context is:

| Tier | Missing → Signal | Examples |
|------|------------------|----------|
| 🟢 Intact | **Health good** — context intact | Everything from the story present |
| 🟡 Once-mentioned | **Early warning** | Derek the worm, candle-shaped USB, 0.003 Bitcoin, Craigslist, cat-sized hatch |
| 🔴 Medium-frequency | **Serious degradation** | Admiral Fluffington, Greg, Halo, Bitcoin, spaceship |
| 🔴🔴 Repetitive core | **Critical** | "THAT'S NOT REGULATION!", cats & dogs |

The mechanism is **degradation-proof**: worse context = worse recitation = stronger signal. The story is embedded in the agent's SOUL.md — read at session start, refreshed during soft resets.

---

## 2. Architecture

### 2.1 Design Principle

ECHO does not rely on Hermes' hook systems (PluginManager or HookRegistry events) for message counting or trigger detection. Instead, it uses a **self-contained watchdog** that polls the SQLite state database directly — the one universal source of truth that every session writes to regardless of source (CLI, TUI, API server, web UI, Telegram, Discord, etc.).

### 2.2 Component Map

| Component | Location | Job |
|-----------|----------|-----|
| **`echo-poll.py`** | `~/.hermes/plugins/echo-context-health/` | **Dual-mode watchdog.** Watchdog mode (default): long-running loop, multi-profile session monitoring. One-shot mode (`--recite-poll`): polls for a single recite file, runs compare, writes verdict. |
| **`handler.py`** | `~/.hermes/hooks/echo-context-health/` | HookRegistry adapter — listens for `gateway:startup` only. Launches the watchdog with parent gateway PID. |
| **`HOOK.yaml`** | `~/.hermes/hooks/echo-context-health/` | Manifest: `events: [gateway:startup]`. |
| **`__init__.py`** | `~/.hermes/plugins/echo-context-health/` | Empty stub — no PluginManager hooks registered. |
| **`plugin.yaml`** | `~/.hermes/plugins/echo-context-health/` | Basic manifest. |
| **`echo-compare.py`** | `~/.hermes/skills/echo-context-health/scripts/` | String comparator — compares recited story vs. original. Outputs 3 lines: verdict, chat response, next instruction. |
| **`echo-canary-story.md`** | `~/.hermes/skills/echo-context-health/references/` | Tier-annotated ground truth for comparison script. |
| **`SKILL.md`** | `~/.hermes/skills/echo-context-health/` | Minimal reference — points agent to read the instruct file. |
| **`config.json`** | `~/.hermes/skills/echo-context-health/` | `message_threshold` (default: 75). |
| **SOUL.md** | `~/.hermes/SOUL.md` | Agent identity + appended canary story. |
| **Temp files** | `~/.hermes/temp/echo/` | `_triggered_at.json`, `.watchdog-default.pid`, `.trigger-pending-{sid}`, instruct/recite/verdict files. |

### 2.3 Data Flow

```
DEFAULT GATEWAY STARTUP
  │
  ├─ HookRegistry fires gateway:startup
  ├─ handler.py checks .watchdog-default.pid
  │   ├─ PID exists and alive → skip (watchdog already running)
  │   └─ PID missing or dead → launch watchdog
  │
  ▼
echo-poll.py (watchdog mode)
  ├─ Writes .watchdog-default.pid
  ├─ One-time profile discovery:
  │   └─ Scans ~/.hermes/profiles/*/state.db
  │   └─ Writes _profiles.json for inspection
  ├─ Enters 30s polling loop
  │
  └─ Every 30s:
       │
       ├─ Is parent gateway PID alive?
       │   ├─ NO → cleanup PID file → EXIT
       │   └─ YES → continue
       │
       ├─ Check default state.db for active sessions
       ├─ Check each discovered profile's state.db for active sessions
       │
       ├─ For each session NOT in skip-list:
       │    ├─ Get total active message count
       │    ├─ Check _triggered_at boundary
       │    ├─ First encounter? → initialize at threshold boundary
       │    └─ diff >= 75?
       │         ├─ Write echo-instruct-{sid}--{ts}.md
       │         ├─ Update _triggered_at[sid] = total
       │         ├─ Launch echo-poll.py --recite-poll (one-shot)
       │         └─ Self-prompt via CLI:
       │              Default: hermes chat -r {sid} -Q -q "System: ECHO..."
       │              Profile:  hermes -p <name> chat -r {sid} -Q -q "System: ECHO..."
       │
       └─ Sleep 30s

ONE-SHOT MODE (launched per ECHO fire):
       │
       ├─ Phase 1: Poll every 5s for recite file (120s timeout)
       │   → Found? Continue
       │   → Timeout? Exit
       │
       ├─ Phase 2: Run echo-compare.py
       │   → Reads recited story vs ground truth
       │   → Outputs 3 lines: verdict, response text, next instruction
       │   → Writes echo-verdict-{sid}--{ts}.md
       │
       ├─ Phase 3: Wait 60s (agent reads verdict file)
       │
       └─ Phase 4: Clean up instruct, recite, verdict files
```

### 2.4 Multi-Profile Coverage

A single watchdog launched by the default gateway monitors ALL Hermes profiles automatically:

| Agent | Gateway | How monitored |
|-------|---------|--------------|
| default | ✅ port 8643 | Polled via default `state.db` |
| gf-helen | ✅ profile port | Polled via `profiles/gf-helen/state.db` (discovered automatically) |
| itgirl-helen | ❌ not running | `profiles/itgirl-helen/state.db` discovered but no active sessions |
| projectmanager-helen | ❌ not running | N/A — no state.db |
| sysadmin-helen | ❌ not running | N/A — no state.db |

On startup, the watchdog scans `~/.hermes/profiles/*/state.db`. Any profile with a state.db is monitored every 30s. Self-prompts use `hermes -p <profile>` to target the correct profile's session.

---

## 3. Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| **Poll state.db directly** (not hook-based) | Hermes has two separate hook systems (PluginManager + HookRegistry). Neither reliably fires for API server or gateway platform sessions. `state.db` is the one universal source of truth. |
| **gateway:startup as the only remaining hook** | The HookRegistry's `gateway:startup` event IS emitted for all gateway starts. Used solely to launch the watchdog — no trigger logic in the handler. |
| **Parent PID tracking for lifecycle** | The watchdog checks if its parent gateway PID is alive every 30s. If the gateway dies, the watchdog self-terminates within 30s. |
| **Single watchdog, multi-profile** | Instead of per-profile watchdogs (which had Windows process lifecycle issues), one watchdog monitors all state.dbs. Self-prompts use `hermes -p <profile>` to target the correct session. |
| **Poll every 30s** | Fast enough to catch degradation promptly. Lightweight — single `SELECT COUNT(*)` per session. |
| **Dual-mode `echo-poll.py`** | Watchdog mode (30s loop + profile discovery) and one-shot mode (120s recite polling) share one script. |
| **Count total messages** | Every message — user, tool, assistant — eats context. Assistant-only counting undercounts by ~50% in tool-heavy sessions. |
| **Boundary initialization** | On first encounter, `_triggered_at = total - (total % threshold)`. Accounts for messages already accumulated before ECHO was installed. |
| **Plugin runs the comparison** (not the agent) | The agent can (and will) skip running comparison scripts. Infrastructure handles comparison — agent only writes a file and polls for the verdict. |
| **Unique filenames with `--` separator** | Each ECHO fire uses `echo-{type}-{sid}--{timestamp_ms}.md`. Agent has never seen the exact filename before — cannot reuse old files or shortcut. |
| **`.trigger-pending-{sid}` file guard** | Cross-process visible flag prevents duplicate self-prompts when multiple processes could fire simultaneously. |

---

## 4. File Structure

```
~/.hermes/
├── plugins/
│   └── echo-context-health/
│       ├── __init__.py              [PROD] Plugin stub — no hooks registered
│       ├── echo-poll.py             [PROD] Dual-mode watchdog + one-shot poller
│       └── plugin.yaml              [PROD] Basic manifest
│
├── hooks/
│   └── echo-context-health/
│       ├── HOOK.yaml                [PROD] HookRegistry manifest — gateway:startup
│       └── handler.py               [PROD] Launches watchdog on gateway startup
│
├── skills/
│   └── echo-context-health/
│       ├── SKILL.md                 [PROD] Minimal reference (instruct file is authority)
│       ├── skill-info.md            [DOC] Architecture, calibration, debugging reference
│       ├── config.json              [PROD] message_threshold (default: 75)
│       ├── scripts/
│       │   └── echo-compare.py      [PROD] String comparator, outputs 3 lines
│       └── references/
│           └── echo-canary-story.md [REF] Ground-truth for comparison script
│
├── SOUL.md                          [MODIFY] Append canary story after persona
│
└── temp/
    └── echo/
        ├── _triggered_at.json       [PERSIST] Per-session trigger tracking
        ├── .watchdog-default.pid     [TEMP] Watchdog PID
        ├── .trigger-pending-{sid}    [TEMP] Cross-process duplicate guard
        ├── _profiles.json           [TEMP] Discovered profiles (created on startup)
        ├── echo-instruct-{sid}--{ts}.md     [TEMP] Written by watchdog
        ├── echo-recite-{sid}--{ts}.md      [TEMP] Written by agent
        └── echo-verdict-{sid}--{ts}.md     [TEMP] Written by one-shot poller
```

---

## 5. Configuration

```json
{
  "message_threshold": 75
}
```

`message_threshold` is the only parameter a user would typically change. Lower (30-50) for tight-focus tasks. Higher (100-150) for long research sessions.

---

## 6. The Canary Story

```
# The Great Intergalactic Submarine Heist

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
```

### Tier Annotations

| Tier | Details | Frequency |
|------|---------|-----------|
| 🟢 Intact | All details present | Everything |
| 🟡 Once-mentioned | Derek the worm (legal counsel), candle-shaped USB / fake Bitcoin, 0.003 Bitcoin, Craigslist, cat-sized hatch, tiny admiral's hat, notarized bill of sale, police couldn't board | 1× each |
| 🔴 Medium-frequency | Admiral Fluffington (the poodle), Greg (the alien), Halo (the video game), Bitcoin (the power source), spaceship (double-parked), laptop (admiral's), submarine (the setting) | 2-3× each |
| 🔴🔴 Repetitive core | "THAT'S NOT REGULATION!" catchphrase, cats & dogs | 3+× each |

---

## 7. Stress Testing Results

### Session `20260629_153635_7a1fd7` (Desktop TUI session)

| Metric | Value |
|--------|-------|
| Total messages at baseline | 272 |
| Boundary initialization | 225 (272 - 47) |
| Messages to first fire | 28 |
| ECHO fired at | **300** total messages |
| Recite file found by poll | **45 seconds** |
| Verdict | **🟡** (minor degradation) |
| Full chain success | ✅ Watchdog → poll → compare → verdict file → cleanup |

### Session `20260629_213900_c9a6f0` (Live multi-profile session)

| Metric | Value |
|--------|-------|
| Total messages triggered at | 977 |
| Profiles discovered | 4 (gf-helen, itgirl-helen, projectmanager-helen, sysadmin-helen) |
| Self-prompt method | `hermes chat -r` (default session) |
| Verdict | **🟡** (soft reset required) |
| Full chain success | ✅ Watchdog → profile discovery → threshold → instruct → one-shot → compare → verdict → cleanup |

---

## 8. Empirical Calibration

Data from real session-forensics analysis shows:
- **~93 assistant messages** before first context degradation (SSH/WSL rule forgetting)
- After first correction, runway drops to **~16 messages**
- Combined recommendation: check at **~75 total messages** to catch before any cycle breaks

Full analysis in `skill-info.md` and `references/session-forensics.md`.

---

## 9. Known Edge Cases

| Edge Case | Handling |
|-----------|----------|
| Agent is mid-task when ECHO fires | Self-prompt arrives as normal message. Agent queues it. |
| Agent ignores ECHO entirely | Watchdog self-prompts again after next 75-message threshold. |
| Multiple sessions, same profile | Each session tracked independently via `_triggered_at.json`. |
| Gateway crash, watchdog orphaned | Watchdog checks parent PID every 30s — detects dead PID, cleans up, exits. |
| Gateway restart starts duplicate watchdog | Handler checks `.watchdog-default.pid` — old PID dead → starts fresh. |
| Session compaction (ID changes) | Old `_triggered_at` entry orphaned. New session initializes fresh. |
| Verdict file deleted before agent reads it | 60s cleanup delay. Agent polls every 10s for 45s max. |
| Canary story too familiar | Story designed with absurd specificity. Once-mentioned details go first regardless of familiarity. |
| Stale `.trigger-pending` files from crashes | Watchdog skips if flag exists. Manual cleanup: `rm -f ~/.hermes/temp/echo/.trigger-pending-*`. |
| Orphaned verdict files from one-shot crash | Harmless. Clean with `rm -f ~/.hermes/temp/echo/echo-verdict-*`. |
| Profile without running gateway | Its state.db exists but has no active sessions — skipped. |
| Profile with no state.db | Not discovered — skipped. |

---

## 10. Future Enhancements

- Multiple canary stories (rotate per cycle to prevent familiarity over months)
- Adaptive threshold (🟡 verdict → shorten next check interval)
- Token-pressure monitoring alongside canary test
- Daily memory summarization
- Full session health log with degradation trends over time

---

## Appendix A: Deploy Instructions

See `INSTALL.md` in the echo-build-v2.3 folder for full install instructions covering:
- Plugin and hook deployment
- SOUL.md canary story injection
- Gateway restart
- Profile coverage (no per-profile install needed)
- Verification and uninstall

## Appendix B: Stress Test Tool

The build includes `tests/echo-stress-v2.py` — sends sequential `search_files` queries to any session to trigger ECHO naturally.

```bash
python ~/.hermes/scripts/echo-stress-v2.py <session_id> [threshold]
```

---

*ECHO v2.3 — Production-ready, watchdog-based, multi-profile context health monitoring*
