"""Shared helpers for Trinity guardrail hooks.

All hooks in /opt/trinity/hooks/ import from this module. It centralises:
- Loading baseline + runtime guardrail config (root-owned, agent cannot rewrite).
- Structured logging to /logs/guardrails.jsonl.
- Safe stdin JSON parsing with a fail-closed default.

Hook protocol (Claude Code):
- stdin: JSON with tool_input, tool_name, hook_event_name, ...
- exit 0 with no stdout: allow
- exit 2 with stderr message: deny (message shown to model)
- exit 1 or uncaught exception: treated as non-blocking error by Claude
  -> we fail-closed by catching and exiting 2 ourselves.
"""
import json
import os
import re
import sys
import time
import traceback
from typing import Any

RUNTIME_CONFIG_PATH = "/opt/trinity/guardrails-runtime.json"
BASELINE_CONFIG_PATH = "/opt/trinity/guardrails-baseline.json"
LOG_PATH = "/logs/guardrails.jsonl"


def log_event(event: str, **fields: Any) -> None:
    """Append a structured JSON event to the guardrails log. Never raises."""
    try:
        record = {"ts": time.time(), "event": event, **fields}
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def load_config() -> dict:
    """Load runtime config, falling back to baseline, then hardcoded defaults.

    The runtime file is written by startup.sh (root-owned 0444) merging the
    image-baked baseline with the AGENT_GUARDRAILS env var.
    """
    for path in (RUNTIME_CONFIG_PATH, BASELINE_CONFIG_PATH):
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                log_event("config_load_error", path=path, error=str(e))
    log_event("config_missing")
    return {}


def read_stdin_json() -> dict:
    """Parse Claude Code hook JSON from stdin. Fail-closed on parse error."""
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as e:
        log_event("hook_stdin_parse_error", error=str(e))
        print("Guardrail: malformed hook input", file=sys.stderr)
        sys.exit(2)


def compile_patterns(patterns: list) -> list:
    """Compile regex patterns. Skips (and logs) any that fail to compile."""
    compiled = []
    for entry in patterns:
        if isinstance(entry, dict):
            pattern, reason = entry.get("pattern", ""), entry.get("reason", "denied")
        else:
            pattern, reason = entry, "denied"
        try:
            compiled.append((re.compile(pattern), reason))
        except re.error as e:
            log_event("bad_baseline_regex", pattern=pattern, error=str(e))
    return compiled


def deny(message: str, **log_fields: Any) -> None:
    """Emit deny decision to Claude Code and log it."""
    log_event("deny", reason=message, **log_fields)
    print(f"Guardrail blocked: {message}", file=sys.stderr)
    sys.exit(2)


def allow() -> None:
    """Allow the tool call."""
    sys.exit(0)


def run_hook(main_fn) -> None:
    """Wrap a hook's main() with fail-closed error handling."""
    try:
        main_fn()
    except SystemExit:
        raise
    except Exception:
        log_event("hook_error", traceback=traceback.format_exc())
        print("Guardrail: internal error (fail-closed)", file=sys.stderr)
        sys.exit(2)
