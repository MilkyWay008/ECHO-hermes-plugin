# ECHO v2.3.1 — Effective Context Health Optimization

**Build and test environment:** Hermes Agent v0.18.x on Windows.
**Not tested on:** Other platforms or older Hermes versions.

## Overview

ECHO (Effective Context Health Optimization) is a self-contained, per-session context health monitoring and remediation system for Hermes Agent. It detects context degradation in long-running sessions and triggers soft resets to restore agent coherence.

The core innovation is a **canary story** — an absurdist tale ("The Great Intergalactic Submarine Heist") with details at four memorability tiers. The agent must recite this story from memory. Which details are missing reveals exactly how degraded the context is.

## Architecture

A **single watchdog** launched by the default gateway on `gateway:startup` polls the SQLite state database directly — the one universal source of truth. It automatically discovers all Hermes profiles and monitors them from one process.

Key design:
- No dependency on Hermes' PluginManager or HookRegistry event hooks for message counting
- Single watchdog covers all profiles (default + gf-helen, itgirl-helen, etc.)
- Self-prompts via `hermes -p <profile> chat -r {sid}` to target correct profile
- Uses `sys.executable` for all subprocess calls — survives Hermes updates
- All paths use `~/.hermes` — no hardcoded usernames

## Components

| File | Purpose |
|------|---------|
| `echo-poll.py` | Dual-mode watchdog (monitoring + one-shot recite polling) |
| `handler.py` | HookRegistry adapter — launches watchdog on gateway:startup |
| `echo-compare.py` | String comparator — compares recited story vs original, outputs 3-line verdict |
| `echo-canary-story.md` | Tier-annotated ground truth story |

## Files

- `INSTALL.md` — Full install, configuration, and uninstall instructions
- `plugin/echo-context-health/` — Watchdog and plugin stub
- `hooks/echo-context-health/` — HookRegistry adapter
- `skill/` — Protocol, config, compare script, and references

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 2.3.1 | 2026-07-18 | Fixed: one-shot subprocess uses sys.executable (not hardcoded "python"). Updated verdict instructions with explicit "STOP. Read files first" format. Changed SOUL.md injection to structured format. |
| 2.3 | 2026-07-01 | Watchdog architecture. Single watchdog, multi-profile. Removed PluginManager hooks. |
