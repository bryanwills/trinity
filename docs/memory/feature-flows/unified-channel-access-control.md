# Feature: Unified Channel Access Control (#311)

## Overview
A single cross-channel access control primitive for chatting with an agent. **Verified email is the unit of identity** — every channel adapter (web public links, Telegram, Slack, future channels) is responsible only for translating its native sender ID into a verified email. Everything downstream — the allow-list, pending access requests, and per-user persistent memory (MEM-001) — keys off that email.

Before #311, each channel had its own ad-hoc access model:
- Web public links: anonymous-by-default with optional CAPTCHA / email verification per link.
- Telegram: bound 1:1 by chat_id, no notion of a verified user identity.
- Slack: bound by workspace OAuth, but no per-user gate.

After #311, all three channels share one gate, one allow-list (`agent_sharing`), and one approval queue (`access_requests`).

## Design Principle
> **An agent owner manages access by email, not by channel.** Approving `alice@example.com` admits her on Telegram, Slack, and web. Each adapter's job is to prove her email; the platform decides whether she is allowed.

> **Group chats can require at least one verified member.** With `group_auth_mode: "any_verified"`, the first verified user "unlocks" the group for everyone.

## User Story
- As an agent owner, I want to gate my agent uniformly across channels so that approving a user once admits them everywhere.
- As a user contacting an agent on Telegram, I want a clear way to verify my email so the owner can recognize me as the same person who emailed them.
- As an agent owner, I want a queue of pending access requests across all channels so I can grant access without leaving Trinity.

## Entry Points
- **Channel inbound**: `src/backend/adapters/message_router.py:138` — `_handle_message_inner` (gate at lines 210-264)
- **Telegram `/login` command**: `src/backend/adapters/telegram_adapter.py:436` — `handle_command`
- **API (policy)**: `GET|PUT /api/agents/{name}/access-policy`
- **API (requests)**: `GET /api/agents/{name}/access-requests`, `POST /api/agents/{name}/access-requests/{id}/decide`
- **UI**: `src/frontend/src/components/SharingPanel.vue:3-74` — Channel Access Policy + pending requests panel

---

## Architecture

```
                        ┌──────────────────────────────────┐
                        │ ChannelAdapter (per channel)     │
                        │ resolve_verified_email(message)  │ ← channel-specific
                        │ prompt_auth(message, agent, tok) │ ← channel-specific
                        └────────────┬─────────────────────┘
                                     │ verified_email | None
                                     ▼
                        ┌──────────────────────────────────┐
                        │ ChannelMessageRouter (gate)      │
                        │  1. require_email + no email →   │
                        │     prompt_auth, return          │
                        │  2. email + agent access  →      │
                        │     proceed                      │
                        │  3. email + open_access   →      │
                        │     proceed                      │
                        │  4. email + restrictive   →      │
                        │     upsert_access_request, reply │
                        │  5. no email + no policy  →      │
                        │     legacy passthrough           │
                        └────────────┬─────────────────────┘
                                     │ proceed
                                     ▼
                        ┌──────────────────────────────────┐
                        │ TaskExecutionService             │
                        │ source_user_email = verified     │
                        │ → MEM-001 keys per email         │
                        └──────────────────────────────────┘
```

| Channel | `resolve_verified_email` source | `prompt_auth` UX |
|---------|---------------------------------|------------------|
| Telegram | `telegram_chat_links.verified_email` (set by `/login`) | HTML message with `/login` instructions |
| Slack | `slack_service.get_user_email(bot_token, user_id)` (workspace OAuth) | Default text (rarely triggered — OAuth always resolves) |
| Web public link | Session token's verified email (6-digit email verification, see [public-agent-links.md](public-agent-links.md)) | Public chat page prompts for email verification before chat is enabled |

Web public-chat runs the same gate inline in `src/backend/routers/public.py` (it doesn't go through the `ChannelMessageRouter`), but uses the identical decision tree and the same `db.email_has_agent_access` / `db.upsert_access_request` primitives. The trigger is the agent-level `agent_ownership.require_email` flag (via `db.get_access_policy`), not a per-link flag.

---

## Database Layer

### Migration: `access_control` (`src/backend/db/migrations.py:1007-1047`)

Idempotent migration that:
1. Adds columns to `agent_ownership`:
   ```sql
   ALTER TABLE agent_ownership ADD COLUMN require_email INTEGER DEFAULT 0;
   ALTER TABLE agent_ownership ADD COLUMN open_access INTEGER DEFAULT 0;
   ```

### Migration: `group_auth_mode` (`src/backend/db/migrations.py`)

Idempotent migration that:
1. Adds `group_auth_mode` to `agent_ownership`:
   ```sql
   ALTER TABLE agent_ownership ADD COLUMN group_auth_mode TEXT DEFAULT 'none';
   ```
2. Adds group verification columns to `telegram_group_configs`:
   ```sql
   ALTER TABLE telegram_group_configs ADD COLUMN verified_by_email TEXT;
   ALTER TABLE telegram_group_configs ADD COLUMN verified_at TEXT;
   ```
2. Adds two columns to `telegram_chat_links` to bind a verified email to a Telegram user:
   ```sql
   ALTER TABLE telegram_chat_links ADD COLUMN verified_email TEXT;
   ALTER TABLE telegram_chat_links ADD COLUMN verified_at TEXT;
   ```
3. Creates the `access_requests` table:
   ```sql
   CREATE TABLE access_requests (
       id TEXT PRIMARY KEY,
       agent_name TEXT NOT NULL,
       email TEXT NOT NULL,
       channel TEXT,
       requested_at TEXT NOT NULL,
       status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | denied
       decided_by INTEGER,
       decided_at TEXT,
       UNIQUE(agent_name, email)
   );
   CREATE INDEX idx_access_requests_agent ON access_requests(agent_name, status);
   CREATE INDEX idx_access_requests_email ON access_requests(email);
   ```

The fresh-install table DDL also lives in `db/schema.py:651-663`, mirroring the migration.

### `AccessPolicyMixin` (`src/backend/db/agent_settings/access_policy.py`)

New mixin composed into `AgentOperations` (`db/agents.py:33-40`):

| Method | Purpose |
|--------|---------|
| `get_access_policy(agent_name)` | Returns `{require_email: bool, open_access: bool, group_auth_mode: str}`. Defaults to `{False, False, "none"}` when the agent has no row. |
| `set_access_policy(agent_name, require_email, open_access, group_auth_mode)` | Updates all three fields atomically on `agent_ownership`. |

**`group_auth_mode` values:**
- `"none"` (default) — Group chats bypass email verification entirely (legacy behavior)
- `"any_verified"` — At least one verified member required before the bot responds to anyone in the group

This follows architectural invariant #2 (Mixin Composition for agent settings): each new agent setting is a new mixin, not a bigger class.

### `AccessRequestOperations` (`src/backend/db/access_requests.py`)

New ops class (not a mixin — `access_requests` is its own domain table):

| Method | Behavior |
|--------|----------|
| `upsert_pending(agent_name, email, channel)` | Inserts a new pending request, or — on UNIQUE collision — resets an existing approved/denied row back to `pending` and refreshes timestamp + channel. Returns the row dict. `channel` values: `"web"`, `"telegram"`, `"slack"`. |
| `list_for_agent(agent_name, status="pending")` | Returns rows ordered by `requested_at DESC`. |
| `get(request_id)` | Single-row lookup. |
| `decide(request_id, approve, decided_by_user_id)` | Sets `status` to `approved` or `denied`, stamps `decided_by` and `decided_at`. |
| `delete_for_agent(agent_name)` | Cascade delete on agent removal. |

### Extended `AgentSharingMixin` (`src/backend/db/agent_settings/sharing.py:121-148`)

Two new helpers — both are lookups by **email** (not by username), which is the cross-channel identity:

```python
def is_agent_shared_with_email(self, agent_name, email) -> bool:
    """Direct hit on agent_sharing.shared_with_email."""

def email_has_agent_access(self, agent_name, email) -> bool:
    """Cross-channel access check (#311).
    True if email is owner, admin, or in agent_sharing.
    """
```

`email_has_agent_access` is the single function the channel router calls to check authorization.

### Extended `TelegramChannelOperations` (`src/backend/db/telegram_channels.py:242-309`)

| Method | Purpose |
|--------|---------|
| `get_chat_link(binding_id, telegram_user_id)` | Returns the chat link row (now including `verified_email`, `verified_at`). |
| `get_verified_email(binding_id, telegram_user_id)` | Convenience: returns `verified_email` or None. |
| `set_verified_email(binding_id, telegram_user_id, email)` | `INSERT OR IGNORE` the chat link row, then UPDATE `verified_email` + `verified_at`. |
| `clear_verified_email(binding_id, telegram_user_id)` | `/logout` — nullifies both columns. |

### Cascade on agent delete (`db/agents.py:117-128`)

`delete_agent_ownership` now also deletes `access_requests` for the agent in the same transaction as `agent_sharing` and `agent_ownership`:

```python
cursor.execute("DELETE FROM agent_sharing WHERE agent_name = ?", (agent_name,))
cursor.execute("DELETE FROM access_requests WHERE agent_name = ?", (agent_name,))  # #311
cursor.execute("DELETE FROM agent_ownership WHERE agent_name = ?", (agent_name,))
```

### Database Facade (`src/backend/database.py`)

New methods on the central `db` singleton:

| Facade method | Delegates to |
|---------------|--------------|
| `get_access_policy(agent_name)` | `_agent_ops.get_access_policy` |
| `set_access_policy(agent_name, require_email, open_access)` | `_agent_ops.set_access_policy` |
| `email_has_agent_access(agent_name, email)` | `_agent_ops.email_has_agent_access` |
| `upsert_access_request(agent_name, email, channel)` | `_access_request_ops.upsert_pending` |
| `list_access_requests(agent_name, status)` | `_access_request_ops.list_for_agent` |
| `get_access_request(request_id)` | `_access_request_ops.get` |
| `decide_access_request(request_id, approve, decided_by_user_id)` | `_access_request_ops.decide` |
| `delete_access_requests_for_agent(agent_name)` | `_access_request_ops.delete_for_agent` |
| `get_telegram_verified_email(binding_id, telegram_user_id)` | `_telegram_ops.get_verified_email` |
| `set_telegram_verified_email(binding_id, telegram_user_id, email)` | `_telegram_ops.set_verified_email` |
| `clear_telegram_verified_email(binding_id, telegram_user_id)` | `_telegram_ops.clear_verified_email` |

---

## Channel Adapter Layer

### `ChannelAdapter` ABC additions (`src/backend/adapters/base.py:169-290`)

Methods on the base class — all have safe defaults, so existing adapters keep working without changes (architectural invariant #9):

```python
async def resolve_verified_email(self, message: NormalizedMessage) -> Optional[str]:
    """
    Translate the channel-native identity into a verified email, if known.

    Returns lowercase email string, or None when the sender has not yet
    proven an email. Default: None.
    """
    return None

async def prompt_auth(
    self,
    message: NormalizedMessage,
    agent_name: str,
    bot_token: Optional[str] = None,
) -> None:
    """
    Ask the sender to prove an email (channel-specific).
    Default: send a generic text reply with /login instructions.
    """

# Group Authentication (group_auth_mode support)

async def is_group_verified(self, message: NormalizedMessage, agent_name: str) -> bool:
    """
    Check if the group chat has at least one verified member.
    Called when group_auth_mode == "any_verified".
    Default: True (allow all — for channels that don't support groups).
    """
    return True

async def set_group_verified(self, message: NormalizedMessage, agent_name: str, email: str) -> None:
    """
    Mark the group as verified by the given email.
    Called when a verified user sends the first message to an unverified group.
    Default: no-op.
    """
    pass

async def prompt_group_auth(self, message: NormalizedMessage, agent_name: str, bot_token: Optional[str] = None) -> None:
    """
    Prompt for group verification (channel-specific).
    Called when group_auth_mode == "any_verified" and no one has verified yet.
    Default: send generic text reply.
    """
```

### Telegram (`src/backend/adapters/telegram_adapter.py`)

**`resolve_verified_email`** (lines 202-212): looks up `verified_email` on the chat link via the agent's binding.

```python
binding = db.get_telegram_binding(agent_name)
return db.get_telegram_verified_email(binding["id"], message.sender_id)
```

**`prompt_auth`** (lines 214-236): sends a Telegram-native HTML message:

```
🔒 This agent requires a verified email.

Send /login your@email.com and I'll email you a 6-digit code.
Then reply with /login 123456 to complete verification.
```

**`/login` state machine** — added to `handle_command` (lines 435-448) and dispatched to `_handle_login_command` (lines 450-519):

```
state                                   action
─────────────────────────────────────────────────────────────────
no pending login                        user sends `/login alice@x.com`
                                        → db.create_login_code(email, 10min)
                                        → EmailService.send_verification_code
                                        → _PENDING_LOGINS[(binding_id, tg_user)] = email
                                        → reply "📧 Sent code to alice@x.com"

pending login                           user sends `/login 123456`
                                        → db.verify_login_code(pending_email, code)
                                        → db.set_telegram_verified_email(...)
                                        → _PENDING_LOGINS.pop(...)
                                        → reply "✅ Verified as alice@x.com"

pending login (wrong code)              user sends `/login 999999`
                                        → reply "❌ Invalid or expired code"
                                        (pending kept; user can retry or restart)

verified                                user sends `/logout`
                                        → db.clear_telegram_verified_email(...)
                                        → reply "👋 Logged out"

any state                               user sends `/whoami`
                                        → reply email or "not verified"
```

The pending-email state lives in an in-memory dict `_PENDING_LOGINS` (line 40) keyed by `(binding_id, telegram_user_id)`. It is **per-process** and lost on backend restart by design — users who lose state simply re-issue `/login email`. The verified email itself is persistent in `telegram_chat_links.verified_email`.

### Slack (`src/backend/adapters/slack_adapter.py:51-67`)

Slack is simpler — workspace OAuth already proves identity, so `users.info` returns a verified email directly:

```python
async def resolve_verified_email(self, message):
    bot_token = self.get_bot_token(message)
    email = await slack_service.get_user_email(bot_token, message.sender_id)
    return email.lower() if email else None
```

No `prompt_auth` override is needed — the default text reply is the fallback for the rare case Slack doesn't return an email (e.g. workspace where bot lacks `users:read.email` scope).

---

## Router Gate (`src/backend/adapters/message_router.py:210-264`)

**Also applied by** `src/backend/routers/public.py` for web public-chat — same decision tree, same primitives (`db.email_has_agent_access`, `db.upsert_access_request`), just invoked inline after the session token resolves the verified email (see `_agent_requires_email` and the gate around line 420–450).

The gate is inserted between step 5 (verification) and step 6 (session creation) in `_handle_message_inner`:

```python
# 5b. Unified cross-channel access gate (Issue #311).
verified_email: Optional[str] = None
if not is_group:                                    # group chats bypass — see below
    try:
        verified_email = await adapter.resolve_verified_email(message)
    except Exception as e:
        verified_email = None

    policy = db.get_access_policy(agent_name)
    require_email = policy.get("require_email", False)
    open_access = policy.get("open_access", False)

    if require_email and not verified_email:
        await adapter.prompt_auth(message, agent_name, bot_token)
        return

    if verified_email and db.email_has_agent_access(agent_name, verified_email):
        pass                                        # owner / admin / shared → proceed
    elif open_access:
        pass                                        # anyone w/ verified email → proceed
    elif verified_email:
        db.upsert_access_request(agent_name, verified_email, channel)
        await adapter.send_response(
            message.channel_id,
            ChannelResponse(text="🔒 Your access request is pending approval. ..."),
            thread_id=message.thread_id,
        )
        return
    # else: no verified email and no policy set → legacy permissive (backward compat)
```

**After the gate**, the router uses the verified email as the source identifier so that cross-channel users converge on the same MEM-001 memory key:

```python
# Step 9 — execute task
source_email = verified_email or adapter.get_source_identifier(message)
```

### Group chat authentication

`is_group = message.metadata.get("is_group", False)` — Group chats have separate authentication logic controlled by `group_auth_mode`:

**`group_auth_mode: "none"` (default)** — Group chats skip email verification entirely. Group access is gated by the bot being added to the group (a manual operator action), not by per-user identity. This preserves the existing TGRAM-GROUP semantics.

**`group_auth_mode: "any_verified"`** — At least one group member must verify their email before the bot responds to anyone:

```python
# In message_router.py step 5b
if is_group:
    group_auth_mode = policy.get("group_auth_mode", "none")
    if group_auth_mode == "any_verified":
        group_verified = await adapter.is_group_verified(message, agent_name)
        if not group_verified:
            verified_email = await adapter.resolve_verified_email(message)
            if verified_email:
                # Sender is verified — unlock the group for everyone
                await adapter.set_group_verified(message, agent_name, verified_email)
            else:
                # No one verified — prompt for auth
                await adapter.prompt_group_auth(message, agent_name, bot_token)
                return
```

Once a verified user "unlocks" a group, the `verified_by_email` is stored in `telegram_group_configs` and all subsequent messages from any group member are allowed.

### Backward compatibility

If `require_email` and `open_access` are both `False` **and** the adapter returns no verified email, the gate falls through and the message proceeds as before. This means existing agents keep working without any opt-in step. Owners enable the gate explicitly via the new policy UI.

---

## API Endpoints

All four are owner-only via the `OwnedAgentByName` dependency (architectural invariant #8).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/agents/{name}/access-policy` | Owner | Returns `{require_email, open_access, group_auth_mode}` |
| PUT | `/api/agents/{name}/access-policy` | Owner | Body: `{require_email, open_access, group_auth_mode}` |
| GET | `/api/agents/{name}/access-requests?status=pending` | Owner | Lists access requests for the agent |
| POST | `/api/agents/{name}/access-requests/{id}/decide` | Owner | Body: `{approve: bool}` |

### Approve flow (`routers/sharing.py:171-220`)

On approval the endpoint:
1. Calls `db.decide_access_request(id, True, user_id)` to mark the request approved.
2. Calls `db.share_agent(agent_name, current_user.username, email)` — idempotent insert into `agent_sharing`. Future messages from this email are admitted by `email_has_agent_access`.
3. If `email_auth_enabled` setting is true, auto-adds the email to the platform whitelist with `source="access_request"`.
4. Broadcasts a `agent_shared` WebSocket event so the Sharing panel refreshes for owners viewing it.

### Pydantic models (`routers/sharing.py:22-42`)

```python
class AccessPolicy(BaseModel):
    require_email: bool
    open_access: bool
    group_auth_mode: str = "none"  # 'none' or 'any_verified'

class AccessPolicyUpdate(BaseModel):
    require_email: bool
    open_access: bool
    group_auth_mode: str = "none"  # 'none' or 'any_verified'

class AccessRequest(BaseModel):
    id: str
    agent_name: str
    email: str
    channel: str | None = None
    requested_at: str
    status: str

class AccessRequestDecision(BaseModel):
    approve: bool
```

These are co-located with the router (slight deviation from invariant #15) because they are scoped to the sharing surface area.

---

## Frontend Layer (`src/frontend/src/components/SharingPanel.vue`)

The Sharing tab on `AgentDetail.vue` already housed Team Sharing + Public Links. #311 prepends a new "Channel Access Policy" section.

### Layout (lines 3-74)

```
┌──────────────────────────────────────────────────────────┐
│ Channel Access Policy                                    │
│  ☐ Require verified email                                │
│      Telegram users must /login; Slack uses workspace    │
│      email; web requires email verification.             │
│  ☐ Open access                                           │
│      Anyone with a verified email may chat without       │
│      owner approval.                                     │
│                                                          │
│  Pending access requests (2)                             │
│   ┌────────────────────────────────────────────────────┐ │
│   │ alice@example.com                                  │ │
│   │ via telegram · 2026-04-12 09:14                    │ │
│   │                          [Approve] [Deny]          │ │
│   └────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────┤
│ Team Sharing                                             │
│  user@example.com  [share]                               │
│  (existing list)                                         │
├──────────────────────────────────────────────────────────┤
│ Public Links (existing PublicLinksPanel)                 │
└──────────────────────────────────────────────────────────┘
```

### Reactive state (lines 226-230)

```javascript
const policy = ref({ require_email: false, open_access: false })
const policyLoading = ref(false)
const pendingRequests = ref([])
const decisionLoading = ref(null)
```

### API calls (lines 232-295)

| Function | Endpoint |
|----------|----------|
| `loadPolicy()` | `GET /api/agents/{name}/access-policy` |
| `updatePolicy(changes)` | `PUT /api/agents/{name}/access-policy` (merges changes into current policy) |
| `loadAccessRequests()` | `GET /api/agents/{name}/access-requests?status=pending` |
| `decideRequest(req, approve)` | `POST /api/agents/{name}/access-requests/{id}/decide` — on approve also calls `loadAgent()` to refresh the shares list |

A `watch(() => props.agentName, ..., { immediate: true })` (lines 305-308) loads both the policy and pending requests when the agent changes.

The component uses raw `axios` (matching the existing pattern in this file) rather than going through `agents.js` store — the policy/requests state is local to this panel and not consumed elsewhere.

---

## Architectural Invariants Touched

| # | Invariant | How #311 honors it |
|---|-----------|-------------------|
| 2 | DB Layer: Class-per-domain with Mixin Composition | New `AccessPolicyMixin` composed into `AgentOperations`; new `AccessRequestOperations` is its own domain class because `access_requests` is its own table. |
| 3 | Schema in `db/schema.py`, migrations in `db/migrations.py` | Both updated. Migration registered as `("access_control", _migrate_access_control)` in the migration list. |
| 8 | Auth pattern: `Depends()` + `OwnedAgentByName` | All four new endpoints use `OwnedAgentByName` for owner-only access. |
| 9 | Channel Adapter ABC | Added `resolve_verified_email` and `prompt_auth` to the ABC with safe defaults. Telegram and Slack override; new channels can override or inherit. |
| 11 | WebSocket events for real-time | `agent_shared` event broadcast on approval so other owner sessions refresh. |
| 13 | Credentials never stored in DB | Verified emails are persisted (they are identity, not secret). The 6-digit `/login` code reuses the existing `email_login_codes` table and `EmailService.send_verification_code` — no new credential storage. |

---

## Side Effects

### WebSocket Broadcasts
| Event | When | Payload |
|-------|------|---------|
| `agent_shared` | Access request approved (calls `db.share_agent` internally) | `{name, shared_with: email}` |

### Email
On `/login email`, an email is sent via `EmailService.send_verification_code` — same path as web email auth (see [email-authentication.md](email-authentication.md)).

### Whitelist auto-add
On approval, if `email_auth_enabled` is true, the email is added to the platform email whitelist with `source="access_request"` (parity with `/share` endpoint).

### Auto-promotion to `agent_sharing`
Approving an access request inserts the email into `agent_sharing` so all future messages from that email — across any channel — are admitted by the existing `email_has_agent_access` check, no further owner action needed.

---

## Error Handling

| Error case | HTTP / Channel response |
|-----------|------------------------|
| `prompt_auth` triggered | Adapter-specific message asking to verify (return early) |
| Verified email but not allowed (restrictive policy) | Channel reply: "🔒 Your access request is pending approval." |
| `/login` with no arg | Telegram reply with usage instructions |
| `/login {bad-email}` | Telegram reply: "That doesn't look like an email address." |
| `/login {6-digit-code}` without pending | Telegram reply: "I don't have a pending login for you." |
| `/login {wrong-code}` | Telegram reply: "❌ Invalid or expired code. Try again or request a new one." |
| `decide` request not found / wrong agent | 404 |
| `decide` user lookup fails | 403 |
| `decide` DB update fails | 500 |
| Channel `resolve_verified_email` raises | Treated as `None` (logged warning), gate continues with no email |

---

## Testing

### Prerequisites
- Backend running with #311 migration applied.
- An agent with a Telegram bot bound (see [telegram-integration.md](telegram-integration.md)).
- Email auth configured (`EMAIL_AUTH_ENABLED=true`) — required for Telegram `/login` verification codes to deliver.

### Test 1: Telegram `/login` happy path
1. As an unverified Telegram user, DM the bot. **Expected**: free passthrough (no policy set yet).
2. As owner via UI: Sharing tab → check "Require verified email". **Expected**: policy persisted.
3. DM the bot again. **Expected**: bot replies with `/login` instructions (`prompt_auth`).
4. Send `/login alice@example.com`. **Expected**: bot replies "📧 Sent a 6-digit code"; email arrives.
5. Send `/login 123456` (the code from the email). **Expected**: bot replies "✅ Verified as alice@example.com".
6. Send a normal message. **Expected**: bot still replies "🔒 Your access request is pending approval".
7. As owner: Sharing tab shows `alice@example.com` in pending requests. Click Approve.
8. As alice on Telegram: send another message. **Expected**: real agent response.

### Test 2: Open access
1. Owner enables both "Require verified email" + "Open access".
2. New user `bob@example.com` does the `/login` flow.
3. After verification, send a message. **Expected**: real agent response (no approval needed). No row in `access_requests`.

### Test 3: Slack workspace
1. Owner enables "Require verified email" only.
2. Slack user (in the workspace) DMs the bot. **Expected**: `slack_service.get_user_email` resolves their workspace email automatically; user immediately enters the gate without any `/login` step.
3. Owner approves once → user can chat freely.

### Test 4: Cross-channel identity
1. Approve `alice@example.com` via the Telegram flow.
2. Alice contacts the same agent on Slack (with the same email in her workspace profile). **Expected**: admitted immediately — no second approval; same MEM-001 memory thread.

### Test 5: Group chat bypass (group_auth_mode: none)
1. Add the bot to a Telegram group.
2. With "Require verified email" on but `group_auth_mode: "none"`, a non-verified group member @mentions the bot. **Expected**: gate is bypassed; agent responds normally.

### Test 6: Group authentication (group_auth_mode: any_verified)
1. Owner sets `group_auth_mode: "any_verified"` via API or UI.
2. Add the bot to a Telegram group.
3. An unverified group member @mentions the bot. **Expected**: bot replies with group auth prompt ("This agent requires at least one verified member...").
4. A verified user (who did `/login` in DM) @mentions the bot. **Expected**: group is unlocked, agent responds. `telegram_group_configs.verified_by_email` is set.
5. Another unverified member @mentions the bot. **Expected**: agent responds (group already verified).

### Test 7: Cascade on agent deletion
```bash
# Delete an agent that had pending access requests
curl -X DELETE http://localhost:8000/api/agents/my-agent -H "Authorization: Bearer $TOKEN"
# Verify access_requests for that agent are gone
sqlite3 ~/trinity-data/trinity.db "SELECT COUNT(*) FROM access_requests WHERE agent_name='my-agent';"
# → 0
```

---

## Related Flows

- **MEM-001 per-user memory** ([public-agent-links.md](public-agent-links.md)) — keyed off `source_user_email`, which is now the verified email. Cross-channel users converge on one memory key automatically.
- **Email authentication** ([email-authentication.md](email-authentication.md)) — the `/login` flow reuses `db.create_login_code`, `db.verify_login_code`, and `EmailService.send_verification_code` from the platform email auth path. The whitelist auto-add on approval matches the existing `/share` endpoint.
- **Agent sharing** ([agent-sharing.md](agent-sharing.md)) — `agent_sharing` is the unified allow-list. Approving an access request just calls `db.share_agent`. The cross-channel `email_has_agent_access` helper extends `is_agent_shared_with_email` with owner+admin checks.
- **Telegram integration** ([telegram-integration.md](telegram-integration.md)) — bindings and chat links remain the durable Telegram-side state; #311 only adds two columns and the `/login` command.
- **Slack channel routing** ([slack-channel-routing.md](slack-channel-routing.md)) — the channel router gate runs uniformly; Slack just resolves email via OAuth.
- **Role model** ([role-model.md](role-model.md)) — orthogonal. Roles control *platform* permissions (who can create agents). Access policy controls *per-agent* channel access.

## Follow-ups

- **Access requests via UI for non-Telegram channels.** Pending requests already work uniformly server-side; we may want richer "where did this user come from" metadata in the requests panel.

---

## Status
Working. Telegram, Slack, and web public-chat all run the same gate.

## Revision History

| Date | Changes |
|------|---------|
| 2026-04-12 | Initial implementation (#311). New migration `access_control`, `AccessPolicyMixin`, `AccessRequestOperations`, ABC additions, Telegram `/login` state machine, Slack `users.info` resolver, four owner endpoints, SharingPanel UI. |
| 2026-04-13 | Web public-chat unified. `routers/public.py` now runs the same gate as `message_router.py`, keyed on `agent_ownership.require_email` instead of per-link `agent_public_links.require_email`. New migration `public_link_require_email_unified` ORs legacy per-link flags into the agent-level flag. Access requests from web use `channel="web"`. Closes the #252 follow-up. |
| 2026-04-15 | Group authentication mode. New `group_auth_mode` field (`"none"` or `"any_verified"`) on access policy. When `any_verified`, groups require at least one verified member before the bot responds. New migration `group_auth_mode` adds column to `agent_ownership` and `verified_by_email`/`verified_at` to `telegram_group_configs`. New ABC methods: `is_group_verified()`, `set_group_verified()`, `prompt_group_auth()`. Router gate applies group auth when `is_group=True`. |
