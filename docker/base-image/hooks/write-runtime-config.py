#!/usr/bin/env python3
"""Merge the baseline guardrails config with per-agent overrides and write
the result to /opt/trinity/guardrails-runtime.json (root-owned, 0444).

Invoked by startup.sh via sudo so the agent user cannot rewrite the runtime
config after startup. Per-agent overrides come from the AGENT_GUARDRAILS env
var (a JSON string set by the backend when creating the container).

Only a small, well-defined set of fields can be overridden — regex lists and
the credential scanner are NOT overridable from env.
"""
import json
import os
import sys

BASELINE_PATH = "/opt/trinity/guardrails-baseline.json"
RUNTIME_PATH = "/opt/trinity/guardrails-runtime.json"

OVERRIDABLE_NUMBERS = {"max_turns_chat", "max_turns_task", "execution_timeout_sec"}
OVERRIDABLE_STRING_LISTS = {"extra_bash_deny", "extra_path_deny", "disallowed_tools"}
MAX_STRING_LEN = 256
MAX_LIST_LEN = 50


def _sanitise_string_list(value) -> list:
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:MAX_LIST_LEN]:
        if isinstance(item, str) and item and len(item) <= MAX_STRING_LEN:
            out.append(item)
    return out


def _sanitise_number(value, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(minimum, min(maximum, n))


def main() -> int:
    try:
        with open(BASELINE_PATH) as f:
            config = json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"guardrails: cannot load baseline: {e}", file=sys.stderr)
        return 1

    raw = os.environ.get("AGENT_GUARDRAILS", "").strip()
    if raw:
        try:
            override = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"guardrails: ignoring malformed AGENT_GUARDRAILS: {e}", file=sys.stderr)
            override = {}

        if isinstance(override, dict):
            if "max_turns_chat" in override:
                config["max_turns_chat"] = _sanitise_number(override["max_turns_chat"], 1, 500)
            if "max_turns_task" in override:
                config["max_turns_task"] = _sanitise_number(override["max_turns_task"], 1, 500)
            if "execution_timeout_sec" in override:
                config["execution_timeout_sec"] = _sanitise_number(
                    override["execution_timeout_sec"], 60, 7200
                )
            for field in OVERRIDABLE_STRING_LISTS:
                if field in override:
                    config[field] = _sanitise_string_list(override[field])

    os.makedirs(os.path.dirname(RUNTIME_PATH), exist_ok=True)
    tmp_path = RUNTIME_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(tmp_path, 0o444)
    os.replace(tmp_path, RUNTIME_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
