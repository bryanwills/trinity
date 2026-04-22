# WhatsApp Integration via Twilio (WHATSAPP-001)

**Issue**: #299
**Status**: Phase 1 MVP (direct messages only)
**Extends**: SLACK-002 `ChannelAdapter` abstraction

Adds WhatsApp as a per-agent channel via Twilio's Programmable Messaging API.
Each agent owner brings their own Twilio AccountSid + AuthToken + from-number —
no platform-level Twilio account required.

## Scope

### In scope (Phase 1)
- One Twilio sender per agent (AccountSid + AuthToken + WhatsApp from-number)
- Inbound DMs → `WhatsAppAdapter` → `ChannelMessageRouter` → `TaskExecutionService`
- Outbound responses via Twilio REST (`POST /Messages.json`, HTTP Basic auth)
- Media in (images, audio, PDFs via Twilio-hosted URLs — SSRF-gated)
- AuthToken encrypted at rest (AES-256-GCM via `CredentialEncryptionService`)
- Webhook HMAC-SHA1 verification via `twilio.request_validator.RequestValidator`
- Twilio Sandbox auto-detected from the well-known number `whatsapp:+14155238886`

### Deferred
- **Phase 2** — unified access control (#311) `/login` flows, verified-email
  plumbing (schema columns ship now so Phase 2 is application-only)
- **Phase 3** — SMS on the same Twilio binding, WhatsApp Business templates
  (for outbound-first messaging outside 24h window), interactive buttons,
  voice-note transcription
- **Out of scope** — WhatsApp group chats (Twilio's WhatsApp API does not
  support them)

## Entry Points

- **Backend router** (authenticated):
  - `GET /api/agents/{name}/whatsapp` — binding status
  - `PUT /api/agents/{name}/whatsapp` — configure credentials
  - `DELETE /api/agents/{name}/whatsapp` — remove binding
  - `POST /api/agents/{name}/whatsapp/test` — verify credentials / send test
- **Backend router** (public, HMAC-verified):
  - `POST /api/whatsapp/webhook/{webhook_secret}` — Twilio inbound webhook
- **Frontend**: Agent Detail → Sharing tab → `WhatsAppChannelPanel.vue`

## Components

| Layer | File |
|-------|------|
| Adapter | `src/backend/adapters/whatsapp_adapter.py` |
| Transport | `src/backend/adapters/transports/twilio_webhook.py` |
| DB ops | `src/backend/db/whatsapp_channels.py` |
| DB facade methods | `src/backend/database.py` (WHATSAPP-001 section) |
| Schema migration | `src/backend/db/migrations.py::_migrate_whatsapp_bindings` |
| Router | `src/backend/routers/whatsapp.py` |
| Main wiring | `src/backend/main.py` (router includes + lifespan start/stop) |
| Settings back-fill | `src/backend/routers/settings.py` (piggybacks on `public_chat_url` save) |
| UI panel | `src/frontend/src/components/WhatsAppChannelPanel.vue` |
| UI mount | `src/frontend/src/components/SharingPanel.vue` |
| New dependency | `twilio==9.10.5` (in `docker/backend/Dockerfile`) |

## Flow: Inbound WhatsApp message

```
WhatsApp user's phone
        │ (Meta's WhatsApp network)
        ▼
Twilio WhatsApp API
        │ POST form-encoded body
        │ X-Twilio-Signature: HMAC-SHA1(AuthToken, URL + sorted params)
        ▼
Cloudflare Tunnel (path-filtered: /api/whatsapp/webhook/*)
        │
        ▼
nginx (sets X-Forwarded-Proto $scheme)
        │
        ▼
uvicorn --proxy-headers (so request.url is https://...)
        │
        ▼
routers/whatsapp.py :: handle_twilio_webhook()
        │
        ▼
TwilioWebhookTransport.handle_webhook()
   1. Resolve binding by webhook_secret
   2. Decrypt AuthToken
   3. RequestValidator.validate()
   4. Dedup by MessageSid
   5. Inject _binding_id + _agent_name
   6. asyncio.create_task(process) — returns 200 empty TwiML
        │
        ▼
WhatsAppAdapter.parse_message() → NormalizedMessage
        │
        ▼
ChannelMessageRouter.handle_message() (unchanged)
        │  — rate limit
        │  — access control (#311 — Phase 2 will wire verified_email)
        │  — session lookup
        │  — file downloads (via adapter.download_file with SSRF allowlist)
        │  — TaskExecutionService.execute_task()
        │
        ▼
WhatsAppAdapter.send_response()
        │
        ▼
Twilio REST: POST /2010-04-01/Accounts/{sid}/Messages.json
        │  (HTTP Basic AccountSid:AuthToken)
        ▼
WhatsApp user's phone
```

## Security

### Webhook authentication
**Two-factor gate**:
1. `webhook_secret` in URL path — random 32-byte token, stored encrypted in DB,
   resolves to the right binding; unknown secrets return 200 (no leak)
2. `X-Twilio-Signature` — HMAC-SHA1 of `URL + sorted form params`, keyed on the
   agent's AuthToken, verified by `twilio.request_validator.RequestValidator`.
   Uses `hmac.compare_digest` internally (constant-time).

### Credential handling
- **AuthToken**: AES-256-GCM encrypted via shared `CredentialEncryptionService`
  (same as Slack/Telegram); never logged in full
- **AccountSid**: stored plaintext (public identifier — appears in URLs by design)
- Log masking: phone numbers rendered as `whatsapp:+141***5309` in error paths

### SSRF defense on media downloads
- Twilio media URLs are fetched with HTTP Basic auth
- URL host must match `*.twilio.com` (allowlist in `whatsapp_adapter._is_twilio_media_url`)
- `follow_redirects=False` on the initial request; a single 30x redirect is
  followed only if the target also passes the allowlist check
- Post-download size validation handled by `message_router`'s existing TOCTOU check

### Proxy-header correctness
- nginx already sets `X-Forwarded-Proto $scheme` (`src/frontend/nginx.conf:30`)
- uvicorn runs with `--proxy-headers --forwarded-allow-ips='*'` so `request.url`
  reconstructs to `https://public.example.com/...` — which is the URL Twilio
  signed. Without these flags, every signature check would fail.

## Deployment Prerequisite

Twilio webhooks arrive at `public.your-domain.com/api/whatsapp/webhook/*`,
which goes through Cloudflare Tunnel path filtering.

**Admins must add the path to the Cloudflare Tunnel ingress rules** (manual
dashboard step — see `docs/requirements/PUBLIC_EXTERNAL_ACCESS_SETUP.md`):

| Route | Service |
|-------|---------|
| `/api/whatsapp/webhook/*` | `http://backend:8000` (or `http://frontend:80` if frontend nginx proxies `/api/*`) |

Without this rule, Twilio webhooks return 404 at the Cloudflare edge and never
reach the backend — the UI surfaces a yellow banner reminding the admin.

### Verification
```bash
PUBLIC=https://public.your-domain.com
# Should NOT return 404 (returns 200 empty TwiML for unknown secret):
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$PUBLIC/api/whatsapp/webhook/test"
```

## Database Schema

```sql
CREATE TABLE whatsapp_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL UNIQUE,
    account_sid TEXT NOT NULL,
    auth_token_encrypted TEXT NOT NULL,          -- AES-256-GCM
    from_number TEXT NOT NULL,                   -- 'whatsapp:+E164'
    messaging_service_sid TEXT,                  -- optional, preferred over from_number
    display_name TEXT,                           -- friendly_name from Twilio
    is_sandbox INTEGER DEFAULT 0,                -- auto-detected
    webhook_secret TEXT NOT NULL UNIQUE,
    webhook_url TEXT,                            -- computed from public_chat_url
    enabled INTEGER DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX idx_whatsapp_bindings_agent ON whatsapp_bindings(agent_name);
CREATE INDEX idx_whatsapp_bindings_webhook ON whatsapp_bindings(webhook_secret);

CREATE TABLE whatsapp_chat_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binding_id INTEGER NOT NULL REFERENCES whatsapp_bindings(id),
    wa_user_phone TEXT NOT NULL,                 -- 'whatsapp:+E164'
    wa_user_name TEXT,                           -- Twilio ProfileName
    session_id TEXT,
    verified_email TEXT,                         -- #311 Phase 2
    verified_at TEXT,
    message_count INTEGER DEFAULT 0,
    last_active TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(binding_id, wa_user_phone)
);
CREATE INDEX idx_whatsapp_chat_links_binding ON whatsapp_chat_links(binding_id);
```

## Twilio Setup (user responsibility)

### Development — Sandbox
1. Twilio Console → Messaging → Try WhatsApp
2. Copy AccountSid + AuthToken
3. Sandbox sender is `whatsapp:+14155238886` (shared across Twilio users)
4. Users opt in by sending `join <sandbox-keyword>` from their phone
5. In Trinity: Agent Detail → Sharing → WhatsApp → paste credentials +
   `whatsapp:+14155238886` as from-number
6. Copy the webhook URL from Trinity → paste into Twilio Sandbox → "When a
   message comes in" (HTTP POST)

### Production
1. Twilio Console → Senders → register WhatsApp sender (requires Meta Business Manager linkage, 24–48h display-name approval)
2. Paste production AccountSid + AuthToken + sender from-number into Trinity
3. Configure the webhook URL on the registered sender in Twilio Console

## Twilio Error Codes (logged, not user-facing)

| Code | Meaning | Our handling |
|------|---------|--------------|
| 21211 | `To` number is not a valid WhatsApp number | Log, skip |
| 21408 | Region not enabled for WhatsApp | Log, skip |
| 63016 | Freeform message outside 24-hour customer-service window | Log — MVP limitation; templates come in Phase 3 |

## Related

- `docs/memory/feature-flows/telegram-integration.md` — closest precedent; diffs noted in this doc
- `docs/memory/feature-flows/slack-channel-routing.md` — SLACK-002 adapter abstraction
- `docs/requirements/PUBLIC_EXTERNAL_ACCESS_SETUP.md` — Cloudflare Tunnel ingress config
- `src/backend/adapters/message_router.py` — channel-agnostic pipeline (unchanged by this feature)
