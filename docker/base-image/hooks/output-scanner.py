#!/usr/bin/env python3
"""PostToolUse hook that scans Bash output for leaked credentials.

Phase 1: log-only. We record which credential pattern matched (pattern NAME,
never the value) so future phases can surface this in the UI. Always exits 0.
"""
import sys

sys.path.insert(0, "/opt/trinity/hooks")
from lib import (  # noqa: E402
    compile_patterns,
    load_config,
    log_event,
    read_stdin_json,
    run_hook,
)


def main() -> None:
    data = read_stdin_json()
    tool_response = data.get("tool_response") or data.get("tool_output") or {}
    if isinstance(tool_response, dict):
        text_parts = [
            str(tool_response.get("stdout", "")),
            str(tool_response.get("stderr", "")),
            str(tool_response.get("output", "")),
        ]
    else:
        text_parts = [str(tool_response)]
    combined = "\n".join(p for p in text_parts if p)
    if not combined:
        sys.exit(0)

    config = load_config()
    named_patterns = config.get("credential_patterns", []) or []
    # credential_patterns entries are {"name": "...", "pattern": "...", "reason": "..."}
    compiled = compile_patterns(
        [{"pattern": e.get("pattern", ""), "reason": e.get("name", "unknown")} for e in named_patterns]
    )

    hits = []
    for pattern, name in compiled:
        if pattern.search(combined):
            hits.append(name)

    if hits:
        log_event(
            "credential_pattern_in_output",
            tool=data.get("tool_name", ""),
            patterns=sorted(set(hits)),
        )

    sys.exit(0)


if __name__ == "__main__":
    run_hook(main)
