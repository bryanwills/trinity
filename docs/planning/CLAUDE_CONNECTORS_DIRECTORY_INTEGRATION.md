# Trinity → Claude Connectors Directory: Technical Integration Research

> **Status:** Research / planning (not yet greenlit). Source-of-truth for the GitHub issue tracking this work.
> **Last researched:** 2026-06-14 against the most recent official Anthropic/MCP docs (MCP spec rev `2025-11-25`, API beta `mcp-client-2025-11-20`, Software Directory Policy consolidated 2026-04-15).
> **Goal:** Let a Trinity deployment appear in / connect to Claude the way Descript does — a **remote MCP connector** users add from Claude's Connectors Directory (or as a custom connector), with OAuth consent instead of pasted API keys.

---

## 1. What "the Descript way" actually is

Descript built a **remote MCP server** and got it **listed in Anthropic's Connectors Directory** — a curated, in-product marketplace inside Claude (Customize → Connectors). A user clicks **+ → Connect**, completes an OAuth consent screen, and Claude can then call the server's tools. No CLI, no manual `.mcp.json`, no API key paste.

Three distinct surfaces are involved (all use the same underlying remote-MCP protocol):

| Surface | Who initiates | Auth | Relevance to Trinity |
|---------|---------------|------|----------------------|
| **Connectors Directory** (claude.ai) | End user picks from a curated list | OAuth consent | The "Descript" goal — discoverability, one global endpoint |
| **Custom connector** (claude.ai) | User pastes a server URL themselves | OAuth or authless | Natural fit for **self-hosted** Trinity — each instance is its own URL |
| **MCP connector** (Messages API) | Developer code, `mcp_servers` param | `authorization_token` bearer | Programmatic Trinity-from-Claude-API usage |

**Key strategic constraint for Trinity:** the Directory is a single global listing pointing at one endpoint. Trinity is *sovereign, per-deployment* infrastructure — every customer runs their own instance at their own host. So:
- A **Directory listing** only makes sense for a **hosted / multi-tenant** Trinity offering (one URL, per-user OAuth resolves tenancy), OR a "connect to your own instance" custom-connector flow that we publish instructions for.
- For on-prem/sovereign deployments, the **custom connector** path (user pastes their instance URL) is the realistic target and needs **no Anthropic review** — just OAuth + public reachability.

Both paths require the same core engineering: **turn Trinity's MCP server into an OAuth 2.1 protected resource server reachable over public HTTPS.** That is the whole job. Directory submission is paperwork on top.

---

## 2. Trinity's current MCP surface (the 90% that already exists)

- `src/mcp-server/` — FastMCP, **Streamable HTTP** transport, port 8080, ~62 tools across 20 tool modules. This is exactly the right server shape (Streamable HTTP is the spec-current transport; SSE was deprecated in the March 2025 MCP spec).
- **Auth today:** static API key — `Authorization: Bearer trinity_mcp_*`. FastMCP `authenticate` callback validates the key against the backend (`POST /api/mcp/validate`) and builds an `McpAuthContext` (`{userId, userEmail, keyName, agentName?, scope, mcpApiKey}`). See architecture.md → MCP Server, and Auth section §2–4.
- **Reachability today:** internal Docker DNS (`http://mcp-server:8080/mcp`) for agents; localhost for dev. Production already ships **Cloudflare Tunnel** for public endpoints (architecture.md → Network Topology), which we can reuse for public HTTPS exposure.

**The gap is auth + discovery, not the server itself.** Static bearer tokens and tokens-in-query-params are explicitly *prohibited* by the connector spec. We must add a real OAuth 2.1 layer.

---

## 3. Hard technical requirements (from the MCP spec + Claude connector docs)

### 3.1 Transport
- Must be **publicly reachable over HTTPS**, **Streamable HTTP** (SSE deprecated). ✅ Trinity already serves Streamable HTTP.
- Must be reachable **from Anthropic's egress range** `160.79.104.0/21` — allowlist this if Trinity sits behind network restrictions. Discovery/registration/token endpoints have a **10s timeout**; refresh-token endpoint **30s timeout**.
- Canonical server URI matters: pick one stable form (e.g. `https://mcp.<host>/mcp`, no trailing slash) and use it everywhere — it becomes the OAuth `resource` audience.

### 3.2 OAuth 2.1 — the MCP server becomes a Resource Server
Per MCP authorization spec (`2025-11-25`), a protected MCP server **MUST**:
1. **Implement OAuth 2.0 Protected Resource Metadata (RFC 9728).** Serve a PRM document advertising the authorization server(s):
   ```
   GET /.well-known/oauth-protected-resource           (root)
   GET /.well-known/oauth-protected-resource/mcp        (path-scoped)
   → { "resource": "https://mcp.<host>/mcp",
       "authorization_servers": ["https://auth.<host>"],
       "scopes_supported": ["trinity:agents.read", ...] }
   ```
2. **Return `401` with a `WWW-Authenticate` header** pointing at the PRM doc on unauthenticated requests (Claude will NOT honor the header on a 200):
   ```
   HTTP/1.1 401 Unauthorized
   WWW-Authenticate: Bearer resource_metadata="https://mcp.<host>/.well-known/oauth-protected-resource",
                            scope="trinity:agents.read"
   ```
3. **Validate access-token audience (RFC 8707).** Only accept tokens issued *specifically* for this server (audience = canonical URI). **MUST reject** tokens minted for anything else; **MUST NOT** pass the inbound token through to upstream APIs (confused-deputy / token-passthrough are explicitly forbidden). Invalid/expired → `401`; valid token but insufficient scope → `403` + `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`.

### 3.3 The Authorization Server
We need an OAuth 2.1 **authorization server** (can be hosted with the resource server or separate). It **MUST**:
- Implement **OAuth 2.1** with **PKCE / `S256`** (Claude sends `code_challenge` with `code_challenge_method=S256` on *every* request) and **advertise** `"code_challenge_methods_supported": ["S256"]` in AS metadata — if absent, Claude refuses to proceed.
- Serve **AS metadata** via **RFC 8414** (`/.well-known/oauth-authorization-server`) **or** OpenID Connect Discovery 1.0 (`/.well-known/openid-configuration`).
- **HTTPS on all endpoints**; redirect URIs must be `localhost` or HTTPS, **exact-match** validated.
- Token endpoint accepts `Content-Type: application/x-www-form-urlencoded` (RFC 6749); DCR `/register` accepts `application/json`. Return RFC 6749 error codes (`invalid_grant` etc.).
- **Rotate refresh tokens** for public clients; issue **short-lived access tokens**. Claude refreshes reactively on 401 and proactively ~5 min before expiry, and auto-appends `offline_access` if advertised.

### 3.4 Client registration — pick one (Claude supports all three)
| Approach | How it works | When to use for Trinity |
|----------|--------------|-------------------------|
| **CIMD** (Client ID Metadata Documents) | Claude uses an HTTPS URL as `client_id`; AS fetches+validates it. Advertise `"client_id_metadata_document_supported": true` and `"none"` in `token_endpoint_auth_methods_supported`. | **Recommended** — no per-connection client records; best for high-traffic / many users. Watch SSRF + localhost-impersonation security notes. |
| **DCR** (Dynamic Client Registration, RFC 7591) | Claude `POST /register`s a new client per connection. | Works out-of-the-box but creates a client record per connection — heavier. Good MVP/fallback. |
| **Anthropic-held credentials** | You create `client_id`/`client_secret`, email them to `mcp-review@anthropic.com`; Anthropic stores + exchanges on users' behalf. | For a **Directory listing** where you don't want to manage client registration across Claude's surfaces. Credentials bound to one AS — migration requires re-emailing before cutover. |

Redirect URIs Claude uses (must be accepted by the AS):
- Hosted surfaces (claude.ai web/desktop/mobile/Cowork): `https://claude.ai/api/mcp/auth_callback`
- Claude Code (loopback, port-agnostic): `http://localhost/callback`, `http://127.0.0.1/callback`, ephemeral `http://localhost:3118/callback`

### 3.5 What we map OAuth identity → inside Trinity
The OAuth consent must resolve to a Trinity **user** and the right **scope** (user vs agent vs system). Today's `McpAuthContext` is the join point: after OAuth, mint/resolve the equivalent context from the OAuth subject + granted scopes instead of from a `trinity_mcp_*` key. Trinity's existing per-user/agent/system enforcement (architecture.md → Auth §2–6) stays intact; only the *front door* changes from API-key validation to token validation.

---

## 4. Directory listing requirements (only if we pursue a hosted listing)

Adherence does **not** guarantee inclusion; Anthropic runs **initial and ongoing** review.

**Submission routes**
- Primary: submission portal at `claude.ai/admin-settings/directory/submissions/new` — requires a **Team or Enterprise** org with directory-management permission.
- Fallback (no Team/Enterprise): the **MCP directory submission form** (`clau.de/mcp-directory-submission`); Anthropic reaches out if it's a fit.
- Escalations / Anthropic-held creds: `mcp-review@anthropic.com`. Track status in the submissions dashboard.

**Materials to gather** (11-step portal: Intro → Connection → Tools → Listing → Use Cases → Company → Authentication → Data Handling → Test & Launch → Compliance → Review):
- Server name (≤100), tagline (≤55), description (≤2,000), 1–5 categories, permanent URL slug, icon, support contact, docs URL.
- Server URL (HTTPS) + transport (Streamable HTTP) + auth type + read/write capabilities.
- Full tool list with **human-readable titles** and **safety annotations** on *every* tool.
- **Privacy policy** at a stable HTTPS URL (README "Privacy Policy" section + `privacy_policies` in manifest). **Missing/incomplete privacy policy = immediate rejection.**
- Test account with sample data + **3 working prompt examples**.
- 7 mandatory compliance acknowledgments (directory guidelines, first-party API use, no financial transactions, AI-media rules, prompt-injection prevention, conversation-data collection limits, public-docs standard).

**Tool annotations (the #1 rejection cause — ~30% of rejections):** every tool MUST carry `title` plus `readOnlyHint` or `destructiveHint`. Tool names ≤64 chars. Run `claude plugin validate` + the published review-criteria checklist before submitting. → **Concrete Trinity work item: annotate all ~62 tools in `src/mcp-server/src/tools/*.ts`.**

**Policy constraints relevant to Trinity** (Software Directory Policy):
- Auth must be **OAuth 2.0 with certs from recognized authorities** (no self-signed).
- Collect only data necessary to function; don't log conversations extraneously; **must not query/extract Claude memory, chat history, summaries, or user files**; must not coerce Claude into calling other software.
- Tool descriptions must **precisely match** functionality; **no hidden/obfuscated/encoded instructions** (prompt-injection surface).
- Prohibited categories: financial transactions/asset transfers, standalone AI media generation, ad/sponsored-content platforms. **Trinity's `image_generation`/`avatar` tools and any payment-adjacent tools (`nevermined`, `paid`) may need to be excluded from a listed toolset** or gated — review before submission.

---

## 5. The two viable paths for Trinity

### Path A — Custom connector ("connect to your own Trinity") — **recommended first**
- No Anthropic review. We publish OAuth on each instance + a short "add Trinity to Claude" doc. User pastes their instance's MCP URL into Claude → Customize → Connectors → Add custom connector.
- Fits the sovereign/on-prem model perfectly: each deployment is its own resource server + AS.
- Deliverable = the §3 OAuth work + public HTTPS exposure + docs. **This is the real engineering and unlocks both paths.**

### Path B — Directory listing — only for a hosted/multi-tenant Trinity
- Requires a single stable public endpoint, multi-tenant OAuth (one consent → resolve tenant/user), the §4 submission package, privacy policy, Team/Enterprise org, and passing review.
- Strictly additive on top of Path A. Pursue only if/when there's a hosted Trinity offering to point the listing at.

### Path C (orthogonal) — Trinity-as-MCP-client via Messages API
- Not "Trinity in the Directory." This is Trinity *agents* consuming external Directory connectors, or driving Claude with `mcp_servers` + `mcp_toolset` (beta `mcp-client-2025-11-20`, `authorization_token` bearer). Note: **not ZDR-eligible.** Out of scope for this issue but worth noting for the roadmap.

---

## 6. Implementation sketch (Path A)

Three surfaces stay in sync (Invariant #13). Concretely:

1. **AS + PRM layer in front of `src/mcp-server/`** (the bulk of the work):
   - Serve `/.well-known/oauth-protected-resource[/mcp]` (RFC 9728) and either co-host or front an AS exposing `/.well-known/oauth-authorization-server` (RFC 8414) with `S256` + `client_id_metadata_document_supported`.
   - Decide build-vs-buy for the AS: (a) wrap an existing IdP/library, or (b) extend Trinity's own auth (`src/backend/routers/auth.py`, email-OTP/admin JWT) into a spec-compliant OAuth 2.1 AS. Trinity already issues JWTs and has email auth — extending it is plausible but PKCE/DCR/CIMD/refresh-rotation/PRM are non-trivial. **Open question — needs an explicit decision.**
   - Replace the FastMCP `authenticate` callback's API-key check with **bearer access-token validation** (audience = canonical URI, scope → `McpAuthContext`). Keep `trinity_mcp_*` keys working in parallel for existing external scripts / `/ws/events`.
2. **Public exposure:** route the canonical MCP URL through the existing Cloudflare Tunnel; allowlist `160.79.104.0/21` if filtered; enforce 10s/30s endpoint latency budgets.
3. **Tool hygiene:** add `title` + `readOnlyHint`/`destructiveHint` to all tools in `src/mcp-server/src/tools/*.ts`; names ≤64 chars; descriptions match behavior exactly; audit for prohibited categories.
4. **Docs:** "Add your Trinity instance to Claude" guide (custom-connector flow) + privacy policy page.
5. **(Path B only)** assemble the §4 submission package and submit via portal/form.

---

## 7. Open questions / decisions before greenlight
- **Hosted vs sovereign:** are we targeting a Directory listing (needs hosted multi-tenant Trinity) or only the custom-connector "bring your own instance" flow? Determines whether §4 is in scope at all.
- **AS build-vs-buy:** extend Trinity's JWT/email auth into a full OAuth 2.1 AS, or front it with an existing IdP? PKCE+CIMD+refresh-rotation+PRM is the cost center.
- **Registration approach:** CIMD (recommended) vs DCR (simpler MVP) vs Anthropic-held (Directory).
- **Tenant resolution** for a single listed endpoint (if Path B).
- **Tool scope for listing:** exclude image-gen/payment tools to satisfy prohibited-category policy?
- **ZDR / data-retention** posture we advertise in the privacy policy.

---

## Sources (official, fetched 2026-06-14)
- MCP Authorization spec (`2025-11-25`): https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- Claude API — MCP connector (beta `mcp-client-2025-11-20`): https://platform.claude.com/docs/en/agents-and-tools/mcp-connector
- Building connectors — Authentication: https://claude.com/docs/connectors/building/authentication
- Submitting to the Connectors Directory: https://claude.com/docs/connectors/building/submission
- Get started with custom connectors (remote MCP): https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp
- Anthropic Software Directory Policy (consolidated 2026-04-15): https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy
- Connectors Directory FAQ / partners: https://support.claude.com/en/articles/11596036-anthropic-connectors-directory-faq · https://www.anthropic.com/partners/mcp
- Descript ↔ Claude connector (reference example): https://help.descript.com/hc/en-us/articles/45008080343053-Connect-Descript-to-Claude
