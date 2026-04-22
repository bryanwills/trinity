# /cso --diff Report — feature/299-whatsapp-twilio

**Date**: 2026-04-22
**Mode**: Daily (8/10 confidence gate)
**Scope**: `--diff` against `main` — 10 modified + 7 new files
**Focus**: Webhook signature verification, credential encryption, SSRF on media, proxy-header trust, new attack surface

## Executive summary

**Zero findings at 8/10 confidence.** The WhatsApp/Twilio channel adapter replicates the proven Telegram/Slack security model, with one upgrade (`--proxy-headers` explicit on uvicorn) and one tightening applied during the pre-landing `/review` pass (GET endpoint moved from plain `get_current_user` to `OwnedAgentByName`).

Two defensive-hardening observations noted for future cross-channel work — not findings.

---

## Phase 0 — Architecture mental model

New component: `WhatsAppAdapter` + `TwilioWebhookTransport`, identical shape to Telegram's adapter/transport pair. Trust boundaries unchanged:
- **Inbound**: Cloudflare Tunnel → nginx → uvicorn (FastAPI). New path `/api/whatsapp/webhook/*` must be whitelisted in Cloudflare ingress (manual step, documented in `PUBLIC_EXTERNAL_ACCESS_SETUP.md`).
- **Outbound**: `httpx` → Twilio REST API over TLS with HTTP Basic `AccountSid:AuthToken`.
- **Credential**: AuthToken → `CredentialEncryptionService` (AES-256-GCM) → SQLite.

## Phase 1 — Attack surface census (diff)

| Category | Added |
|----------|-------|
| Public unauthenticated endpoints | 1 (`POST /api/whatsapp/webhook/{webhook_secret}`, HMAC-gated) |
| Authenticated endpoints | 4 (GET/PUT/DELETE/POST test) — `OwnedAgentByName` |
| New secret types at rest | 1 (Twilio AuthToken, encrypted) |
| New outbound hosts | 1 (`api.twilio.com` + `*.twilio.com`) |
| New dependency | `twilio==9.10.5` |
| Dockerfile runtime change | `--proxy-headers --forwarded-allow-ips='*'` |

## Phase 2 — Secrets archaeology (diff)

Scanned branch diff + untracked files for AWS/OpenAI/GitHub/Slack/Twilio secret patterns. **Clean.** Test file uses clearly synthetic tokens (`"test_auth_token_a" * 2`, `"AC00000000000000000000000000000000"`).

## Phase 3 — Supply chain (new dep)

**`twilio==9.10.5`** (released 2026-04-14).
- Snyk: no known CVEs in 9.x
- NVD: no advisories matching 9.x
- Transitive deps (all standard, already in our tree or trivially safe): `requests>=2`, `PyJWT>=2,<3`, `aiohttp>=3.8.4`, `aiohttp-retry>=2.8.3`
- Used only for `from twilio.request_validator import RequestValidator` — the `twilio.rest.Client` surface (largest LOC) is never imported

**No finding.** Dependency footprint acceptable.

## Phase 4 — CI/CD

No workflow files changed.

## Phase 5 — Infrastructure (Dockerfile diff)

```diff
-    python-magic==0.4.27
+    python-magic==0.4.27 \
+    twilio==9.10.5
...
-CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
+CMD ["uvicorn", "main:app", ..., "--proxy-headers", "--forwarded-allow-ips=*"]
```

- Non-root execution, `CAP_DROP: ALL`, security opts — all unchanged ✅
- `--proxy-headers` is REQUIRED for Twilio signature validation to work behind nginx + Cloudflare Tunnel (the URL Twilio signs is `https://...` but backend would otherwise see `http://...`)
- **`--forwarded-allow-ips='*'`**: trusts `X-Forwarded-*` from any upstream. In our topology, backend is reachable only via nginx from outside the Docker network (no host-published port in prod). An attacker with code execution on the Docker network could spoof forwarded-proto — but still cannot forge HMAC without the AuthToken. No concrete exploit. **Defense-in-depth hardening** (see D1 below).

## Phase 6 — Webhook & integration audit (MAIN FOCUS)

Traced the verification path in `adapters/transports/twilio_webhook.py`:

1. Line 95 — Resolve binding by `webhook_secret` (from URL). Unknown → return 200 (no oracle) ✅
2. Line 101–105 — Read form body; malformed → return 200 (no retry amplification) ✅
3. Line 110–116 — Decrypt AuthToken. Decrypt failure → log (no ciphertext leak) and drop ✅
4. Line 119 — `RequestValidator(auth_token)` — validator from `twilio>=9.10.5`
5. Line 121 — Reconstruct URL honoring `X-Forwarded-Proto` ✅
6. Line 123 — `validator.validate(url, params, signature)` — returns bool
7. Line 131 — On fail, return `{"ok": False, "status": 403}` → router raises `HTTPException(403)` ✅

**Verified the validator internals** (`twilio/request_validator.py`):
- `validate()` loops through both `with_port` and `without_port` URL variants (Twilio inconsistency)
- `compare()` is **constant-time**: checks length first, then `result &= c1 == c2` loops through full zip — equivalent to `hmac.compare_digest` for this purpose
- Handles empty-value params correctly (the exact reason we took the dependency)

**Dedup ring** (`_SEEN_MESSAGE_SIDS`, cap 2048, 10% eviction): asyncio-safe under single-worker uvicorn (no awaits between check and insert).

**Response body**: Empty TwiML `<Response/>` with `application/xml` — agent reply delivered asynchronously via REST. No SSRF reflection, no content in webhook response.

**No finding.**

## Phase 7 — LLM & AI security

- WhatsApp message body passes through `ChannelMessageRouter._handle_message_inner` — **unchanged** channel-agnostic path. User content goes into user-message position (not system prompt).
- Restricted tools apply: `channel_allowed_tools` setting (default `WebSearch,WebFetch`) limits agent capability when invoked from channels. Files: same guard-rails apply — images inline base64, other files gated through upload dir with MIME magic-byte validation (pre-existing `message_router.py` code).
- No new prompt-injection surface.

**No finding.**

## Phase 9 — OWASP Top 10 (targeted)

### A01 Broken Access Control
- Webhook: public by design (Twilio can't authenticate); gate is HMAC ✅
- `GET /api/agents/{name}/whatsapp`: **tightened during /review** from `Depends(get_current_user)` → `OwnedAgentByName`. Other endpoints (PUT/DELETE/POST test): already `OwnedAgentByName` ✅
- No horizontal escalation vector identified

### A02 Cryptographic Failures
- AuthToken: AES-256-GCM via existing `CredentialEncryptionService` ✅
- HMAC-SHA1 for signature: Twilio spec (not our choice). SHA-1 here is one-shot message authentication with 128-bit AuthToken — not collision-exploitable in this use case. Modern Twilio supports SHA-256 via `X-Twilio-Signature-256` header, but not yet default; RequestValidator handles both.

### A03 Injection
- All DB writes parameterized with `?` ✅ (zero `f"..."` / `.format` into SQL in new code)
- No `subprocess`, `os.system`, or shell calls introduced
- Vue panel: no `v-html`, inputs auto-escaped by Vue ✅

### A05 Security Misconfiguration
- `--proxy-headers` enabled — previously missing
- `--forwarded-allow-ips='*'` — acceptable given Docker network isolation (see D1 for hardening)

### A07 Authentication
- MCP key / JWT auth unchanged
- No new credential types in cookies or local storage

### A08 Data Integrity
- Agent templates / skills: unaffected
- No new deserialization surface (form-encoded body via `await request.form()` — urlencoded string parser, not pickle)

### A09 Logging & Monitoring
- Log masking for phone numbers (`whatsapp:+141***5309`) ✅
- AuthToken never logged in any path ✅
- Webhook decisions (accept/reject/dedup) logged at appropriate levels
- No platform-audit-log entries for WhatsApp webhooks — matches Telegram precedent (see D2)

### A10 SSRF
- Media downloads gated by `_is_twilio_media_url`: scheme `https` only, host must match `*.twilio.com` suffix
- Test coverage: rejects `attacker.com`, `eviltwilio.com`, `api.twilio.com.evil.com`, `http://` scheme, garbage URLs
- Redirect handling: `follow_redirects=False`; if 30x, target is re-validated via the same allowlist before a single manual follow. Without credentials on the redirect request (Twilio signs the S3 URL with query params)
- **Verified.** No SSRF vector.

## Phase 10 — STRIDE (new webhook receiver)

| Threat | Mitigation |
|--------|------------|
| **Spoofing** | HMAC-SHA1 over URL + form params, keyed on encrypted AuthToken. Constant-time compare. ✅ |
| **Tampering** | HMAC covers all form fields. Tampered body → signature mismatch → 403. ✅ |
| **Repudiation** | Webhook accept/reject decisions logged. Full request audit could be added — D2 below. |
| **Info Disclosure** | Unknown-secret returns 200 (no oracle). Phone numbers masked in logs. AuthToken never in responses/logs. ✅ |
| **DoS** | Cloudflare edge + empty-TwiML 200 response prevents retry storms. Dedup caps processing. Per Phase 12 rules, DoS is excluded from findings. ✅ |
| **Elevation** | No escalation path from webhook to API surface. Channel-scoped allowed_tools apply. ✅ |

## Phase 11 — Data classification

| Type | Sensitivity | Storage | Protection |
|------|-------------|---------|------------|
| Twilio AuthToken | RESTRICTED | `whatsapp_bindings.auth_token_encrypted` | AES-256-GCM, never logged, never returned |
| Twilio AccountSid | INTERNAL | plaintext in DB + logs (truncated) | By design (public identifier) |
| webhook_secret | INTERNAL | plaintext in DB + webhook URL | Security derived from unguessable 256-bit entropy |
| WhatsApp phone number | CONFIDENTIAL | `whatsapp_chat_links.wa_user_phone` + session prompts | Masked in error-path logs; not publicly exposed |
| Message content | CONFIDENTIAL | `chat_messages` (pre-existing) | User-scoped access control |

## Phase 12 — Active verification summary

| Focus area | Status |
|------------|--------|
| HMAC-SHA1 webhook signature (incl. empty-param case) | **VERIFIED** via library source + unit tests |
| AuthToken encryption round-trip | **VERIFIED** via reuse of existing AES-256-GCM primitive |
| SSRF allowlist strictness | **VERIFIED** via 6 spoof-vector tests |
| Constant-time signature compare | **VERIFIED** via library source (no early exit) |
| AuthToken non-disclosure | **VERIFIED** — grep of `router/adapter/transport/db` shows no `logger.*auth_token` |
| Proxy-header correctness | **VERIFIED** — `--proxy-headers` added; nginx sets `X-Forwarded-Proto` |
| Auth boundary on config endpoints | **VERIFIED** — `OwnedAgentByName` on all mutating + GET after /review fix |

---

## Findings

**Count at 8/10 confidence: 0 (zero) CRITICAL, 0 HIGH, 0 MEDIUM.**

---

## Defensive hardening observations (not findings)

These would strengthen posture but have no concrete exploit path under current topology.

### D1 — Narrow `--forwarded-allow-ips`
File: `docker/backend/Dockerfile:68`
Observation: `--forwarded-allow-ips='*'` trusts X-Forwarded-* from any upstream. In the current topology, nginx is the only legitimate upstream and sits inside the same Docker network. An attacker with code execution on the Docker network could spoof `X-Forwarded-Proto: https` — but still cannot forge HMAC without the AuthToken.
Improvement: Narrow to the nginx container's DNS name or the Docker network CIDR (`172.28.0.0/16`).
Priority: low. Not scheduled in this PR.

### D2 — Platform audit log for WhatsApp webhooks
Observation: Accept/reject webhook decisions are logged to Vector but not written to `audit_log` (SEC-001). Same gap exists for Telegram — worth a unified cross-channel pass to add `AuditEventType.EXTERNAL_MESSAGE` for all webhook transports.
Priority: low. Separate cross-cutting work; not scheduled in this PR.

---

## Trend tracking

- Prior report: `cso-2026-04-05.json` (full daily audit)
- This report: `--diff` scoped, not directly comparable
- New findings introduced by this branch: 0
- Pre-existing findings persisting: N/A (diff scope)

## Remediation roadmap

No critical/high findings — no remediation required to merge.

D1 and D2 should be tracked as separate hardening issues after merge.

---

## Files audited

```
docker/backend/Dockerfile                              (modified — deps + uvicorn flags)
docs/memory/architecture.md                            (doc only)
docs/memory/feature-flows.md                           (doc only)
docs/memory/feature-flows/whatsapp-integration.md      (doc only)
docs/memory/requirements.md                            (doc only)
docs/requirements/PUBLIC_EXTERNAL_ACCESS_SETUP.md      (doc only)
src/backend/adapters/transports/twilio_webhook.py      (NEW — signature verify, dedup)
src/backend/adapters/whatsapp_adapter.py               (NEW — adapter, SSRF guard)
src/backend/database.py                                (modified — facade methods only)
src/backend/db/migrations.py                           (modified — new migration)
src/backend/db/whatsapp_channels.py                    (NEW — DB ops, encryption reuse)
src/backend/main.py                                    (modified — router + lifespan wiring)
src/backend/routers/settings.py                        (modified — backfill hook)
src/backend/routers/whatsapp.py                        (NEW — config endpoints)
src/frontend/src/components/SharingPanel.vue           (modified — import only)
src/frontend/src/components/WhatsAppChannelPanel.vue   (NEW — UI panel)
tests/test_whatsapp_adapter.py                         (NEW — 38 tests)
```

---

*Generated by `/cso --diff`. This is an AI-assisted scan. For production systems handling sensitive data, engage a professional penetration testing firm.*
