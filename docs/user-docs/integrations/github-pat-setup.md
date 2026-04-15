# GitHub Personal Access Token Setup

Configure a GitHub Personal Access Token (PAT) to enable repository creation, code sync, and issue management for your Trinity agents.

## Concepts

- **Personal Access Token (PAT)** -- A GitHub credential that grants Trinity access to your repositories. Acts as a password for API access.
- **Classic Token** -- The traditional PAT format. Simpler to configure, grants broad access per scope.
- **Fine-Grained Token** -- Newer PAT format. Allows granular permissions on specific repositories.
- **Scopes** -- Permission categories that control what the token can do (read code, write issues, etc.).

## When You Need a PAT

A GitHub PAT is required for:

| Feature | Requires PAT |
|---------|--------------|
| Create new GitHub repo for an agent | Yes |
| Push agent code to GitHub | Yes |
| Pull updates from private repos | Yes |
| Create/update GitHub Issues from agents | Yes |
| Clone from public templates | No |
| Pull from public repos (read-only) | No |

## How It Works

### Option A: Classic Token (Recommended for Simplicity)

Classic tokens use broad permission scopes. Best when you want agents to access all your repositories.

**Step 1: Create the token**

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **Generate new token** → **Generate new token (classic)**
3. Set a descriptive name: `Trinity Platform`
4. Set expiration (90 days recommended, or "No expiration" for persistent setups)
5. Select these scopes:

| Scope | Required | Purpose |
|-------|----------|---------|
| `repo` | **Yes** | Full control of repositories — read/write code, issues, PRs |
| `workflow` | Optional | Trigger GitHub Actions (if agents manage CI/CD) |
| `read:org` | Optional | Read organization membership (for org repos) |

6. Click **Generate token**
7. **Copy the token immediately** — you cannot view it again

**Step 2: Configure in Trinity**

1. Go to **Settings** in Trinity (sidebar → Settings)
2. Find the **GitHub Personal Access Token (PAT)** section
3. Paste your token
4. Click **Test** to verify it works
5. Click **Save**

The test shows your GitHub username and confirms repo access.

### Option B: Fine-Grained Token (Recommended for Security)

Fine-grained tokens let you limit access to specific repositories and permissions. Best for production environments or when agents should only access designated repos.

**Step 1: Create the token**

1. Go to [github.com/settings/tokens?type=beta](https://github.com/settings/tokens?type=beta)
2. Click **Generate new token**
3. Set a descriptive name: `Trinity Platform`
4. Set expiration
5. Under **Repository access**, choose:
   - **All repositories** — Token works with any repo you own/administer
   - **Only select repositories** — Pick specific repos for Trinity agents

6. Under **Permissions**, expand **Repository permissions** and set:

| Permission | Access Level | Purpose |
|------------|--------------|---------|
| **Contents** | Read and write | Push/pull code, read files |
| **Issues** | Read and write | Create and manage issues |
| **Metadata** | Read-only | Required (auto-selected) |
| **Pull requests** | Read and write | Optional: create PRs |
| **Workflows** | Read and write | Optional: manage GitHub Actions |

7. Click **Generate token**
8. **Copy the token immediately**

**Step 2: Configure in Trinity**

Same as Option A — paste in Settings, test, save.

### For Organization Repositories

If you're creating repos in a GitHub Organization:

1. The PAT must belong to a user with **admin access** to the organization
2. The organization may require **SSO authorization** for the token:
   - After creating the token, click **Configure SSO** next to it
   - Authorize for your organization
3. For fine-grained tokens, the organization must enable them in Settings → Personal access tokens

## Choosing Between Token Types

| Consideration | Classic Token | Fine-Grained Token |
|---------------|---------------|-------------------|
| Setup complexity | Simple (check boxes) | More steps |
| Repository scope | All repos by default | Can limit to specific repos |
| Permission granularity | Broad scopes | Individual permissions |
| Organization support | Full | Requires org admin approval |
| Token format | `ghp_...` | `github_pat_...` |
| Recommended for | Development, personal use | Production, shared teams |

## For Agents

### Checking PAT Status

```
GET /api/settings/api-keys
```

Returns:
```json
{
  "github": {
    "configured": true,
    "masked": "ghp_****xxxx",
    "source": "settings"
  }
}
```

### Testing a PAT

```
POST /api/settings/api-keys/github/test
Content-Type: application/json

{
  "api_key": "ghp_your_token_here"
}
```

Returns:
```json
{
  "valid": true,
  "username": "your-github-username",
  "has_repo_access": true
}
```

### Saving a PAT

```
PUT /api/settings/api-keys/github
Content-Type: application/json

{
  "api_key": "ghp_your_token_here"
}
```

### MCP Tool

```
initialize_github_sync(agent_name, repo_url)
```

Uses the configured PAT to create/connect a GitHub repository for the agent.

## Troubleshooting

### "Resource not accessible by personal access token"

**Cause:** Token lacks required permissions.

**Fix (Classic):** Regenerate with `repo` scope checked.

**Fix (Fine-Grained):** Add "Contents: Read and write" permission.

### "Must have admin rights to Repository"

**Cause:** For organization repos, your user needs admin access to the org.

**Fix:** Ask an org admin to grant you admin access, or create the repo in your personal account.

### "SSO authorization required"

**Cause:** Organization uses SAML SSO and the token isn't authorized.

**Fix:**
1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Find your token
3. Click **Configure SSO**
4. Click **Authorize** for the organization

### Token works in test but fails for specific repo

**Cause (Fine-Grained):** The repo isn't in the token's allowed repository list.

**Fix:** Edit the token and add the repository under "Repository access."

### "Bad credentials" error

**Cause:** Token is expired, revoked, or mistyped.

**Fix:** Generate a new token and reconfigure in Trinity Settings.

## Security Best Practices

1. **Use fine-grained tokens in production** — Limit blast radius if token is compromised
2. **Set expiration dates** — 90 days is a reasonable balance
3. **Use separate tokens per environment** — Dev and prod should have different tokens
4. **Never commit tokens to git** — Trinity stores the PAT in the database, not in code
5. **Rotate tokens periodically** — Update in Settings when you regenerate
6. **Revoke unused tokens** — Clean up old tokens at github.com/settings/tokens

## Limitations

- Trinity stores one platform-wide GitHub PAT. All agents share it.
- Fine-grained tokens require GitHub to be configured to allow them (enabled by default for personal accounts).
- Organization-owned fine-grained tokens require org admin approval.
- Token permissions cannot be changed after creation — regenerate if you need different access.

## See Also

- [GitHub Sync](github-sync.md) — Using git sync after PAT is configured
- [Creating Agents](../agents/creating-agents.md) — Creating agents from GitHub templates
- [Platform Settings](../operations/dashboard.md) — Other settings configuration
