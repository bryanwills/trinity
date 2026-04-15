# GitHub Sync

Keep agents in sync with GitHub repositories using two modes: Source mode (pull-only, default) and Working Branch mode (bidirectional).

## Concepts

- **Source Mode** (default): Pull-only. The agent pulls from the repo but never pushes. Used for deploying agent code from a canonical source.
- **Working Branch Mode**: Bidirectional. The agent has its own branch and can push changes back. Used for agents that modify their own code.
- **Branch Selection**: Specify a branch via URL syntax `github:owner/repo@branch` during creation, or via the `source_branch` parameter in MCP.

## How It Works

### Creating an agent with sync

Agents created from a GitHub template automatically get sync configured. The default mode is Source (pull-only).

### Using sync in the UI

1. Open the agent detail page to see Git status (branch, last sync, pending changes).
2. Click **Pull** to fetch the latest commits from the remote.
3. Click **Sync** to run a full sync operation (pull-only in Source mode; pull + push in Working Branch mode).
4. View the git log to inspect recent commits.

### Initializing sync for existing agents

Agents created without a GitHub repository can be connected after the fact:

- Use the GitHub repo initialization flow in the UI.
- Via MCP: `initialize_github_sync(agent_name, repo_url)`

## For Agents

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents/{name}/git/status` | GET | Git sync status |
| `/api/agents/{name}/git/sync` | POST | Trigger sync |
| `/api/agents/{name}/git/log` | GET | Recent commits |
| `/api/agents/{name}/git/pull` | POST | Pull from remote |

MCP tool: `initialize_github_sync(agent_name, repo_url)`

## See Also

- [GitHub PAT Setup](github-pat-setup.md) — Configure a Personal Access Token before using sync
- [Creating Agents](../agents/creating-agents.md) — Creating agents from GitHub templates
