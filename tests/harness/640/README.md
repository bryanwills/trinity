# Repro harness for #640 — MCP stdio stdout pollution

> **Status:** harness only. The fix at the agent-runtime layer is still
> open. PR #662 is the diagnostic-instrumentation prerequisite.

## What this harness is for

Issue #640 is the open root-cause ticket behind a family of "long execution
silently fails with null response" reports (#678, #630, #618, #548, #586).
The hypothesis is: an stdio MCP server (or one of its descendants) writes
to a file descriptor that ends up interleaving with Claude Code's own
stdout, corrupting the `stream-json` line that carries the result block.

PR #662's author tried to repro with `@upstash/context7-mcp@latest`, ran
~1.5h, did not manifest the failure (issue body says ≥100 turns / 18min
needed). What's missing is a **deterministic** repro: a controlled MCP
server that exhibits each hypothesised leak path, so we can prove which
one actually corrupts the wire and design a fix at the right layer.

## Files

| File | Purpose |
|------|---------|
| `noisy_mcp_server.py` | Minimal stdio MCP server with `--leak <variant>` knobs. Self-contained, stdlib only. |
| `run_repro.py` | Driver that hits an agent's chat API for N turns and measures null-cost / null-response rate. |
| `README.md` | This file. |

## Leak variants

Each variant tests one hypothesis from the #640 issue body and the #662
author's empirical notes:

| Variant | Hypothesis tested |
|---------|-------------------|
| `none` | Control / baseline. Should produce 0% null-cost. |
| `stderr-flood` | MCP child stderr noise — does it bleed onto stdout via Claude's stderr handling? |
| `setsid-child` | Grandchild escapes Claude pgid kill (#618 fix family) and retains write end on protocol pipe. |
| `proc-fd-write` | Process writes to its own fd 1 via `/proc/self/fd/1`, interleaving raw text with MCP frames. |
| `delayed-stdout` | Partial-line writes that race Claude's reader at line boundary. |
| `npm-wrapper` | Real-world `npx`-style: package-manager boilerplate on stdout BEFORE protocol handshake. Most likely culprit. |

The simulator never writes to stdout/stderr for its own diagnostics —
those are reserved for the wire. All instrumentation goes to a sidecar
log file (default `/tmp/noisy-mcp-server.log`).

## How to run

### 1. Pick an agent

You need an existing Trinity agent in your local stack. Either reuse one
or create a fresh template-based agent. The agent's `.mcp.json` will be
modified to attach this simulator.

### 2. Wire the simulator into the agent's `.mcp.json`

Inside the agent container (e.g. `docker exec -u developer <agent> bash`):

```bash
# Copy the simulator into the agent's home dir.
docker cp tests/harness/640/noisy_mcp_server.py <agent>:/home/developer/noisy_mcp_server.py
docker exec <agent> chmod +x /home/developer/noisy_mcp_server.py

# Append to .mcp.json (variant=npm-wrapper is the most production-like).
# Use jq or hand-edit to add:
#   "noisy": {
#     "command": "/home/developer/noisy_mcp_server.py",
#     "args": ["--leak", "npm-wrapper", "--log-file", "/home/developer/noisy-mcp.log"]
#   }
docker exec <agent> python3 -c '
import json, pathlib
p = pathlib.Path("/home/developer/.mcp.json")
cfg = json.loads(p.read_text())
cfg.setdefault("mcpServers", {})["noisy"] = {
    "command": "/home/developer/noisy_mcp_server.py",
    "args": ["--leak", "npm-wrapper", "--log-file", "/home/developer/noisy-mcp.log"],
}
p.write_text(json.dumps(cfg, indent=2))
'
```

### 3. Restart the agent so Claude picks up the new MCP

```bash
# Force agent-server to restart claude — easiest path is to reset the chat session.
curl -X DELETE -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/agents/<agent>/chat/history
```

### 4. Drive the repro

```bash
./tests/harness/640/run_repro.py --agent <agent> --turns 50
```

The driver mints a JWT from `.env` `ADMIN_PASSWORD` if `--token` is not
passed.

### 5. Read the verdict

The driver prints per-turn status and a summary:

```
============================================================
Total turns         : 50
Null cost           : 6 (12.0%)
Null response       : 4 (8.0%)
Mean turn duration  : 14.3s
Max turn duration   : 78.1s

First 5 failures:
  turn 17: {'turn': 17, 'cost': None, ...}

FAIL: null-cost rate 12.0% exceeds threshold 5.0%
```

Exit code 0 if null-cost rate is below the `--null-cost-fail-rate`
threshold (default 5%), 1 otherwise. Once a fix lands the same harness
becomes a regression gate — variants that pass on the fixed runtime but
failed on the buggy runtime are the ones the fix actually addresses.

## Caveats

- The simulator runs as the agent user (`developer`, uid 1000). It needs
  Python 3 in `$PATH` — already present in the base image.
- `setsid-child` forks a grandchild that holds an open write end on the
  protocol pipe. After Claude exits, this grandchild becomes the orphan
  that PR #662's `_kill_orphan_pipe_writers` is designed to surface.
  Verify by checking `/home/developer/noisy-mcp.log` for the
  `setsid-child started pgid=…` line and then `/proc/<pid>/fd/` after a
  failed run.
- The `npm-wrapper` variant only fires once at startup. To stress this
  path, you must reset the chat session between turns so Claude
  re-spawns the MCP child. The driver currently does not — extend if
  needed.
- Until PR #662 lands, parse_failures aren't logged at WARNING level —
  the driver measures the symptom (null cost / null response). Once #662
  ships, also grep agent-server logs for `parse_failures=` to correlate.

## Links

- Issue #640 — root cause ticket
- PR #662 — diagnostic instrumentation (prerequisite)
- Issue #678 — production manifestation (24-min execution, null telemetry)
- `docker/base-image/agent_server/services/claude_code.py` — spawn site
- `docker/base-image/agent_server/utils/subprocess_pgroup.py` — orphan reaper
