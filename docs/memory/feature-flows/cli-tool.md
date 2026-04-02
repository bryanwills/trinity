# CLI Tool (CLI-001)

> **Status**: Phase 1 — core commands
> **Issue**: #231
> **Docs**: `docs/CLI.md`

## Overview

Python Click CLI (`trinity`) that provides shell-level access to the Trinity platform. Thin HTTP client to the existing FastAPI backend — same API surface as the MCP server but invoked via Bash instead of MCP tool calls.

```
User/Agent/Script → trinity CLI → HTTP → FastAPI Backend (:8000)
```

## Multi-Instance Profiles

The CLI supports named profiles for managing multiple Trinity instances (local dev, staging, production).

### Config Format

```json
{
  "current_profile": "localhost",
  "profiles": {
    "localhost": {
      "instance_url": "http://localhost:8000",
      "token": "eyJ...",
      "user": {"email": "admin@example.com"}
    },
    "trinity.example.com": {
      "instance_url": "https://trinity.example.com",
      "token": "eyJ...",
      "user": {"email": "user@example.com"}
    }
  }
}
```

### Profile Commands

| Command | Description |
|---------|-------------|
| `trinity profile list` | Show all profiles with active indicator |
| `trinity profile use <name>` | Switch active profile |
| `trinity profile remove <name>` | Delete a profile |

### Profile Resolution Priority

1. `TRINITY_URL` / `TRINITY_API_KEY` env vars (always win)
2. `--profile <name>` global flag
3. `TRINITY_PROFILE` env var
4. `current_profile` in config file

### Backwards Compatibility

Legacy flat configs (`{"instance_url": "...", "token": "..."}`) are auto-migrated to a `default` profile on first access.

## Authentication Flow

### `trinity init` (onboarding)

```
User runs `trinity init [--profile name]`
  → Prompt: instance URL
  → GET /api/auth/mode (verify reachable)
  → Derive profile name from hostname (or use --profile)
  → Prompt: email
  → POST /api/access/request {email}     ← NEW ENDPOINT (auto-whitelist)
  → POST /api/auth/email/request {email} ← existing email auth
  → Prompt: 6-digit code
  → POST /api/auth/email/verify {email, code}
  → Store JWT + user in profile within ~/.trinity/config.json (0600)
  → Set as active profile
  → Ready
```

### `trinity login` (returning user)

```
User runs `trinity login [--profile name]`
  → Uses stored instance URL from profile (or --instance flag)
  → POST /api/auth/email/request
  → POST /api/auth/email/verify
  → Update stored JWT in profile
```

### Token Resolution

Priority order:
1. `TRINITY_API_KEY` env var
2. Active profile's token in `~/.trinity/config.json`
3. Error: "Run trinity init"

Instance URL:
1. `TRINITY_URL` env var
2. Active profile's instance_url in `~/.trinity/config.json`
3. Error: "Run trinity init"

## Backend: Access Request Endpoint

**File**: `src/backend/routers/auth.py`
**Endpoint**: `POST /api/access/request`
**Auth**: None (public)

```
Request:  {"email": "user@example.com"}
Response: {"success": true, "message": "Access granted", "already_registered": false}
```

- Auto-adds email to whitelist via `db.add_to_whitelist(email, added_by="admin", source="cli")`
- Idempotent: returns `already_registered: true` if exists
- Rate limited: reuses `check_login_rate_limit(client_ip)` — 5 req / 10 min per IP
- Requires: setup completed, email auth enabled

## HTTP Client

**File**: `src/cli/trinity_cli/client.py`

`TrinityClient` wraps httpx with:
- Auto-inject `Authorization: Bearer <token>` header
- 401 → "Run trinity login" message
- HTTP errors → `TrinityAPIError` with status + detail
- Unauthenticated variants for login flow (`post_unauthenticated`, `get_unauthenticated`)

## Output Formatting

**File**: `src/cli/trinity_cli/output.py`

- `--format json` (default): `json.dumps(data, indent=2)`
- `--format table`: Rich table rendering
  - Lists → column headers from dict keys
  - Dicts → key/value two-column table

## Command Map

| CLI Command | HTTP Method | Backend Endpoint |
|-------------|-------------|------------------|
| `trinity profile list` | — | local config |
| `trinity profile use <name>` | — | local config |
| `trinity profile remove <name>` | — | local config |
| `trinity agents list` | GET | `/api/agents` |
| `trinity agents get <name>` | GET | `/api/agents/{name}` |
| `trinity agents create <name>` | POST | `/api/agents` |
| `trinity agents delete <name>` | DELETE | `/api/agents/{name}` |
| `trinity agents start <name>` | POST | `/api/agents/{name}/start` |
| `trinity agents stop <name>` | POST | `/api/agents/{name}/stop` |
| `trinity agents rename <old> <new>` | PUT | `/api/agents/{name}/rename` |
| `trinity chat <agent> "msg"` | POST | `/api/agents/{name}/chat` |
| `trinity history <agent>` | GET | `/api/agents/{name}/chat/history` |
| `trinity logs <agent>` | GET | `/api/agents/{name}/logs` |
| `trinity health fleet` | GET | `/api/monitoring/status` |
| `trinity health agent <name>` | GET | `/api/monitoring/agents/{name}` |
| `trinity skills list` | GET | `/api/skills/library` |
| `trinity skills get <name>` | GET | `/api/skills/library/{name}` |
| `trinity schedules list <agent>` | GET | `/api/agents/{name}/schedules` |
| `trinity schedules trigger <agent> <id>` | POST | `/api/agents/{name}/schedules/{id}/trigger` |
| `trinity tags list` | GET | `/api/tags` |
| `trinity tags get <agent>` | GET | `/api/agents/{name}/tags` |

## Installation

```bash
pip install -e src/cli/
```

Registers `trinity` console script via `pyproject.toml` `[project.scripts]`.

## Architecture

```
src/cli/
├── pyproject.toml              # Package definition, console_scripts entry
├── trinity_cli/
│   ├── __init__.py             # Version
│   ├── main.py                 # Click group, --profile global option
│   ├── client.py               # TrinityClient (httpx wrapper, profile-aware)
│   ├── config.py               # Profile-based config, legacy migration
│   ├── output.py               # JSON/table formatting (Rich)
│   └── commands/
│       ├── auth.py             # init, login, logout, status (profile-aware)
│       ├── profiles.py         # list, use, remove
│       ├── agents.py           # list, get, create, delete, start, stop, rename
│       ├── chat.py             # chat, history, logs
│       ├── health.py           # fleet, agent
│       ├── skills.py           # list, get
│       ├── schedules.py        # list, trigger
│       └── tags.py             # list, get
```

## Future Phases

- Phase 2: Remaining ~47 MCP tool equivalents (credentials, events, executions, systems, subscriptions, notifications, nevermined)
- Phase 3: `trinity deploy` for agent deployment from current directory
- Phase 3: Shell completions (Click supports bash/zsh/fish completion generation)
