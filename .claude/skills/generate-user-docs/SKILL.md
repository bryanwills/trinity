---
name: generate-user-docs
description: Generate and update user documentation from code, feature flows, and recent changes into docs/user-docs/
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - Agent
user-invocable: true
---

# Generate User Docs

Generate and maintain `docs/user-docs/` — the authoritative user and agent documentation for Trinity, derived from code as the single source of truth.

## Purpose

Read backend routers, frontend views, feature flows, and recent changes to produce clear, non-redundant, MECE documentation organized for two audiences: human users (UI workflows) and agent users (API and programmatic usage).

## State Dependencies

| Source | Location | Read | Write |
|--------|----------|------|-------|
| Backend routers | `src/backend/routers/*.py` | Yes | No |
| Frontend views | `src/frontend/src/views/*.vue` | Yes | No |
| Feature flows | `docs/memory/feature-flows/*.md` | Yes | No |
| Feature flow index | `docs/memory/feature-flows.md` | Yes | No |
| Requirements | `docs/memory/requirements.md` | Yes | No |
| Architecture | `docs/memory/architecture.md` | Yes | No |
| Trinity Docs site | `../trinity-docs/app/getting-started/*.tsx` | Yes | No |
| Abilities repo | `github.com/abilityai/abilities` (README) | Yes | No |
| Git history | `git log --since` | Yes | No |
| Existing user docs | `docs/user-docs/**/*.md` | Yes | Yes |

## Prerequisites

- Repository checked out with `docs/memory/` populated
- No build or runtime dependencies required

## Maintained Guides

These tutorial-style guides walk users through end-to-end tasks. Keep them in sync with their source.

| Guide | Source | Purpose |
|-------|--------|---------|
| `guides/deploying-trinity.md` | `trinity-docs/app/getting-started/deploying-trinity/page.tsx` | Cloud vs self-hosted setup |
| `guides/using-trinity.md` | `trinity-docs/app/getting-started/using-trinity/page.tsx` | UI tour: dashboard, agents, monitoring |
| `guides/building-agents.md` | `trinity-docs/app/getting-started/building-agents/page.tsx` | Create, develop, deploy with abilities |

**Sync rule**: When the trinity-docs source changes, update the corresponding guide to match. Convert TSX to markdown, preserving structure and content.

## Target Structure

```
docs/user-docs/
├── README.md                          # Index + navigation
├── guides/                            # Tutorial-style walkthroughs
│   ├── deploying-trinity.md          # Cloud vs self-hosted setup
│   ├── using-trinity.md              # UI tour: dashboard, agents, monitoring
│   └── building-agents.md            # Create, develop, deploy with abilities
├── getting-started/
│   ├── overview.md                    # What is Trinity, key concepts
│   ├── setup.md                       # First-time setup, login, admin config
│   └── quick-start.md                # Create first agent in 5 minutes
├── agents/
│   ├── creating-agents.md            # Templates, GitHub repos, manual
│   ├── managing-agents.md            # Start/stop, rename, delete, health
│   ├── agent-chat.md                 # Chat interface, streaming, history
│   ├── agent-terminal.md             # Web terminal, SSH access
│   ├── agent-files.md                # File browser, virtual filesystem
│   ├── agent-logs.md                 # Log viewing, telemetry
│   └── agent-configuration.md        # Config tab, environment, runtime
├── credentials/
│   ├── credential-management.md      # Adding, editing, hot-reload
│   ├── oauth-credentials.md          # OAuth2 flow, Google setup
│   └── subscription-credentials.md   # Shared credentials across agents
├── collaboration/
│   ├── agent-network.md              # Multi-agent systems, DAGs
│   ├── agent-permissions.md          # Who can call whom
│   ├── event-subscriptions.md        # Pub/sub between agents
│   └── system-manifest.md           # System-wide configuration
├── automation/
│   ├── scheduling.md                 # Cron schedules, execution queue
│   ├── skills-and-playbooks.md       # Skills library, assignment, playbooks
│   └── approvals.md                  # Human-in-the-loop approval gates
├── operations/
│   ├── dashboard.md                  # Main dashboard, timeline view
│   ├── operating-room.md             # Events, costs, system alerts
│   ├── monitoring.md                 # Health checks, system metrics
│   └── executions.md                # Execution list, detail, logs
├── sharing-and-access/
│   ├── agent-sharing.md              # Share with users, read-only mode
│   ├── public-links.md              # Public chat links, anonymous access
│   ├── tags-and-organization.md      # Tags, filtering, system views
│   └── mobile-admin.md              # Mobile PWA at /m
├── integrations/
│   ├── github-sync.md               # Git sync, branch support
│   ├── slack-integration.md          # Slack channels, routing
│   ├── mcp-server.md                # MCP tools, API keys
│   └── nevermined-payments.md        # x402 payments, monetization
├── advanced/
│   ├── voice-chat.md                 # Voice via Gemini Live API
│   ├── image-generation.md           # Platform image generation
│   ├── agent-avatars.md              # AI-generated avatars
│   └── dynamic-dashboards.md         # Custom agent dashboards
└── api-reference/
    ├── authentication.md             # JWT tokens, API keys, auth flow
    ├── agent-api.md                  # Agent CRUD, lifecycle endpoints
    ├── chat-api.md                   # Chat, voice, streaming endpoints
    └── webhook-triggers.md           # Remote triggers, event webhooks
```

## Process

### Step 1: Inventory Current State

Read what already exists in `docs/user-docs/` and build a checklist of files that need creating or updating.

```bash
find docs/user-docs -name "*.md" -type f 2>/dev/null | sort
```

### Step 2: Read Source Material

Read these sources to extract current feature state. Use parallel agents where possible.

**2a. Feature flows** — Read `docs/memory/feature-flows.md` (the index) to know which flows exist, then read individual flows as needed per section.

**2b. Requirements** — Read `docs/memory/requirements.md` for the canonical feature list and acceptance criteria.

**2c. Architecture** — Read `docs/memory/architecture.md` for system design context, component relationships, and data flow.

**2d. Recent changes** — Get recent changes from git history:
```bash
git log --oneline --since="2 weeks ago" | head -30
```
This identifies what has changed recently and which docs may need updating.

**2e. Backend routers** — Glob `src/backend/routers/*.py` and read router files relevant to the section being written. Extract:
- Endpoint paths and HTTP methods
- Request/response patterns
- Business logic and validation rules

**2f. Frontend views** — Glob `src/frontend/src/views/*.vue` and read views relevant to the section being written. Extract:
- UI layout and tab structure
- User-facing labels and actions
- State management patterns

### Step 3: Generate/Update Documentation

For each section in the target structure, produce or update the markdown file following these rules:

#### Writing Rules

1. **MECE structure** — Each section covers a mutually exclusive, collectively exhaustive slice of functionality. No concept is explained in two places. If a concept spans sections, explain it once and cross-reference.

2. **Dual audience format** — Each doc follows this template:

```markdown
# [Feature Name]

[1-2 sentence summary of what this feature does and why it matters]

## Concepts

[Define key terms specific to this feature. Only terms not defined elsewhere.]

## How It Works

[Step-by-step explanation for human users. Describe UI workflows with
screen locations ("click the **Agents** tab in the sidebar"). Include
what the user sees at each step.]

## For Agents

[Programmatic usage. API endpoints with methods and paths. Link to
API docs at `/docs` (Swagger) for full request/response schemas.
Include example cURL or SDK snippets only when the pattern is
non-obvious.]

**API Endpoints**: See [Backend API Docs](http://localhost:8000/docs) for full schemas.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/...` | GET | ... |

## Limitations

[Known constraints, edge cases, or things that don't work yet.
Only include if meaningful.]

## See Also

[Cross-references to related docs in this folder. Use relative links.]
```

3. **No redundancy** — Do not repeat information from other docs. Cross-reference instead. The `Concepts` section in `getting-started/overview.md` is the canonical glossary; other docs reference it rather than re-defining terms.

4. **Code-derived accuracy** — Every claim must trace to code or a feature flow. Do not invent features. If a feature flow says "planned" or a router has TODO comments, note it as upcoming rather than documenting it as available.

5. **Clear, direct tone** — Active voice. Short sentences. No filler ("In order to", "It should be noted that"). Say what happens, not what "can" happen.

6. **Placeholder values** — Use `your-domain.com`, `your-api-key`, `user@example.com` in all examples. Never include real credentials or internal URLs.

### Step 4: Generate README.md Index

Create `docs/user-docs/README.md` as the entry point:

```markdown
# Trinity User Documentation

> Auto-generated from source code. Run `/generate-user-docs` to update.

## Getting Started
- [Overview](getting-started/overview.md) — What is Trinity
- [Setup](getting-started/setup.md) — Installation and first login
- [Quick Start](getting-started/quick-start.md) — Create your first agent

## Agents
- [Creating Agents](agents/creating-agents.md)
- [Managing Agents](agents/managing-agents.md)
...
```

List every file with a one-line description. Group by section folder.

### Step 5: Diff Review (Approval Gate)

**STOP and present changes to the user before writing.**

Show:
- Files created (new)
- Files updated (with summary of what changed)
- Files unchanged (skipped)

Ask: "Write these changes?" Only proceed after confirmation.

### Step 6: Write Files

Create directories and write all approved files:

```bash
mkdir -p docs/user-docs/{getting-started,agents,credentials,collaboration,automation,operations,sharing-and-access,integrations,advanced,api-reference}
```

Write each markdown file using the Write or Edit tool.

### Step 7: Verify

```bash
find docs/user-docs -name "*.md" -type f | wc -l
```

Confirm the expected number of files were created/updated. Report the final count.

## Completion Checklist

- [ ] All sections in target structure have corresponding files
- [ ] Every doc follows the dual-audience template (How It Works + For Agents)
- [ ] No redundant explanations across docs (MECE verified)
- [ ] All API references link to Swagger (`/docs`) rather than duplicating schemas
- [ ] No real credentials, internal URLs, or PII in any doc
- [ ] README.md index is complete and links are valid
- [ ] Changes reviewed by user before writing

## Error Recovery

| Error | Recovery |
|-------|----------|
| Feature flow missing for a section | Write doc from router code + view code; note "No feature flow available" |
| Router has no docstrings | Read function bodies and URL patterns to infer behavior |
| Conflicting info between requirements and code | Trust code (source of truth); note discrepancy |
| Existing doc is manually edited | Preserve manual edits; append auto-generated sections below a separator |
| Section has no corresponding code yet | Mark as "Planned" with brief description from requirements.md |

## Self-Improvement

After completing this skill's primary task, consider tactical improvements:

- [ ] **Review execution**: Were there friction points, unclear steps, or inefficiencies?
- [ ] **Identify improvements**: Could error handling, step ordering, or instructions be clearer?
- [ ] **Scope check**: Only tactical/execution changes — NOT changes to core purpose or goals
- [ ] **Apply improvement** (if identified):
  - [ ] Edit this SKILL.md with the specific improvement
  - [ ] Keep changes minimal and focused
- [ ] **Version control** (if in a git repository):
  - [ ] Stage: `git add .claude/skills/generate-user-docs/SKILL.md`
  - [ ] Commit: `git commit -m "refactor(generate-user-docs): <brief improvement description>"`
