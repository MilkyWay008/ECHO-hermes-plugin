"""ECHO Plugin — Effective Context Health Optimization.

This plugin is a STUB. All ECHO monitoring is handled by echo-poll.py
which runs as a long-lived watchdog, launched by the HookRegistry
adapter on gateway:startup.

The watchdog polls state.db every 30s, checks all active sessions against
the threshold, and fires ECHO when needed. No PluginManager hooks needed.
"""

def register(ctx) -> None:
    """Plugin entry point — no hooks registered (watchdog handles everything)."""
    pass
