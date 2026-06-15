# Trinity: Open Source vs Enterprise

> Feature comparison between the open-source (OSS) edition and Trinity
> Enterprise, including enterprise features currently in development or on the
> roadmap. Sourced from `docs/memory/architecture.md`,
> `docs/memory/feature-flows/enterprise-modules.md`,
> `docs/planning/ENTERPRISE_ARCHITECTURE.md`,
> `docs/planning/OSS_ENTERPRISE_SPLIT_RESEARCH.md`, and the
> [Enterprise epic #1049](https://github.com/abilityai/trinity/issues/1049).

---

## TL;DR

Trinity is **open-core**. The open-source edition is the complete platform —
agent orchestration, scheduling, channels, monitoring, security primitives, and
all core APIs. Enterprise adds **compliance and organizational-governance
modules** on top, loaded from a private submodule. Enterprise includes
everything in open source.

---

## Feature comparison

Legend: ✅ Included · 🔶 In development · 📋 Planned · — Not available

| Feature | Open Source | Enterprise |
|---|:---:|:---:|
| **Agent platform** | | |
| Autonomous agent deployment (isolated Docker containers, self-hosted) | ✅ | ✅ |
| Agent templates (GitHub-native) & fleet management | ✅ | ✅ |
| Web chat with persistent, queryable conversation history | ✅ | ✅ |
| Resumable sessions (agent memory preserved across turns) | ✅ | ✅ |
| Voice sessions & VoIP telephony (outbound agent phone calls) | ✅ | ✅ |
| Agent terminal, file manager, SSH access, outbound file sharing | ✅ | ✅ |
| Sequential agent loops (bounded multi-run tasks) | ✅ | ✅ |
| Agent-to-agent collaboration with explicit permissions | ✅ | ✅ |
| **Orchestration & reliability** | | |
| Cron scheduling, webhook triggers, conditional pre-checks | ✅ | ✅ |
| Execution tracking, per-agent analytics, cost observability | ✅ | ✅ |
| Capacity management, task queueing & backlog | ✅ | ✅ |
| Circuit breakers, idempotency keys, watchdog recovery | ✅ | ✅ |
| Canary invariant monitoring (continuous orchestration health checks) | ✅ | ✅ |
| Fleet health monitoring, heartbeat liveness, Operating Room queue | ✅ | ✅ |
| **Channels & integrations** | | |
| Slack, Telegram, WhatsApp integrations | ✅ | ✅ |
| Public agent links & public chat | ✅ | ✅ |
| MCP server (113+ tools), REST API, CLI, A2A agent card | ✅ | ✅ |
| OpenTelemetry tracing & centralized log aggregation | ✅ | ✅ |
| **Users & access control** | | |
| Multi-user with 4-tier role model (user/operator/creator/admin) | ✅ | ✅ |
| Email OTP + admin login, email whitelist | ✅ | ✅ |
| Agent sharing & cross-channel access requests | ✅ | ✅ |
| Scoped, revocable MCP API keys | ✅ | ✅ |
| User & organization management (invite, deactivate/reactivate, per-user activity view) | — | ✅ |
| Two-factor authentication (2FA/TOTP) | — | 📋 Planned ([#388](https://github.com/abilityai/trinity/issues/388)) |
| SSO (SAML / OIDC) | — | 📋 Planned |
| SCIM 2.0 user provisioning | — | 📋 Planned |
| Advanced RBAC / org-workspace model | — | 📋 Planned |
| **Security & compliance** | | |
| AES-256-GCM credential encryption, file-injection credential model | ✅ | ✅ |
| Hardened containers (non-root, network isolation, rate limiting) | ✅ | ✅ |
| Platform audit log API (append-only, hash chain, export, verification) | ✅ | ✅ |
| Audit log dashboard (admin compliance viewer) | — | ✅ |
| SIEM log export (stream audit log to your SIEM) | — | 🔶 In development ([#997](https://github.com/abilityai/trinity/issues/997)) |
| Extended & configurable data-retention policy | community default | 📋 Planned ([#1039](https://github.com/abilityai/trinity/issues/1039)) |
| License management (offline-verified, air-gap friendly) | — | 📋 Planned ([#1040](https://github.com/abilityai/trinity/issues/1040)) |
| **Monetization** | | |
| Paid agent chat via x402 / Nevermined | ✅ | ✅ |
| Subscription credential management | ✅ | ✅ |
| **Data & infrastructure** | | |
| SQLite (default) + PostgreSQL backend (experimental) | ✅ | ✅ |
| Sovereign deployment — your hardware, no phone-home | ✅ | ✅ |

Shipped enterprise modules correspond to feature ids registered at runtime:
`audit` (audit dashboard, [#941](https://github.com/abilityai/trinity/issues/941)),
`user_management` ([#995](https://github.com/abilityai/trinity/issues/995)),
`siem` ([#997](https://github.com/abilityai/trinity/issues/997), in development).
The authoritative list for any given instance is
`GET /api/settings/feature-flags` → `enterprise_features`.

---

## Technical appendix — how the split works

### The model

The public repo ([abilityai/trinity](https://github.com/abilityai/trinity))
contains the full platform. Enterprise backend modules live in a **private git
submodule** (`Abilityai/trinity-enterprise`) mounted at
`src/backend/enterprise/`. A clone without the submodule boots as OSS-only with
no code changes and no degraded core functionality.

Two details that often surprise people:

- **The audit log itself is OSS.** Storage, append-only triggers, hash chain,
  retention, and all `/api/audit-log/*` endpoints live in the public repo —
  enterprise adds the dashboard UI and SIEM export on top.
- **Enterprise UI code is also OSS.** The Vue views for enterprise pages ship
  in the public frontend bundle (they contain no algorithmic IP) and are hidden
  server-side via feature flags. Only the enterprise *backend* is private.

### Issue tracking (two-tracker model)

The split extends to the issue tracker, by **issue type**:

| Issue type | Tracker | Visibility |
|------------|---------|------------|
| `type-bug` · `type-refactor` · `type-docs` | `abilityai/trinity` | public |
| `type-feature` · `type-epic` | `abilityai/trinity-enterprise` | private |

Bugs and core maintenance stay open to the community; features and epics (product
direction) are private. **Tracker ≠ code repo** — a private feature is usually
implemented by a PR in the *public* core repo that references the issue as
`abilityai/trinity-enterprise#N`; only the roadmap intent is private. Community
feature ideas arrive via public [Discussions](https://github.com/abilityai/trinity/discussions)
and maintainers triage accepted ones onto the roadmap. Full rules:
`.claude/DEVELOPMENT_WORKFLOW.md` → Repository Routing.

### Runtime gating

One seam, three pieces, all in the OSS repo:

1. **Conditional import** (`src/backend/main.py`):
   `try: from enterprise.backend import register_enterprise; register_enterprise(app)
   except ImportError: <OSS-only, log and continue>`.
2. **`EntitlementService`** (`services/entitlement_service.py`): in-memory
   registry. Each enterprise module calls `register_module("<feature_id>")` at
   boot; OSS-only builds never reach that code, so the registry stays empty.
3. **Gates**: `requires_entitlement("<feature_id>")` FastAPI dependency on every
   enterprise endpoint; the `enterprise_features` list on
   `GET /api/settings/feature-flags` drives frontend visibility (Pinia
   `stores/enterprise.js` + NavBar + router guard on `meta.requiresEntitlement`).

Observable behavior by build:

| | OSS-only (submodule absent) | Enterprise (submodule present) | `TRINITY_OSS_ONLY=1` override |
|---|---|---|---|
| `enterprise_features` flag | `[]` | registered feature ids | `[]` even when registered |
| `/api/enterprise/*` endpoints | **404** (routers never mounted) | 200 (entitled) | **403** "not licensed" |
| Enterprise nav/login/views | Hidden (+ route guard redirects) | Visible | Hidden |
| Core platform | Fully functional | Fully functional | Fully functional |

`TRINITY_OSS_ONLY=1` is a hard compliance lockdown: the submodule may be
mounted, but every entitlement check denies. CI exercises the OSS-only path
explicitly (`.github/workflows/build-without-submodule.yml`).

### Architectural ground rules

These keep the OSS edition first-class rather than a crippled demo:

- **Backend-only private.** Enterprise IP is backend logic (compliance flows,
  export pipelines, future license verification). Frontend ships in OSS,
  gated server-side.
- **Two-track migrations** (Invariant #3). Enterprise modules own only
  `enterprise_*` tables, migrated by a separate runner tracked in
  `enterprise_schema_migrations`. Enterprise migrations may FK into OSS tables
  but must **never ALTER** an OSS table.
- **Core-primitive + enterprise-knob.** Anything OSS must *enforce* lands as an
  edition-agnostic OSS primitive; enterprise only adds the management surface.
  Example: `users.suspended_at` — the column and login/token enforcement are
  OSS, only the deactivate/reactivate setter is enterprise (#995).
- **Degrade-to-Community.** A missing/revoked submodule or future expired
  license turns enterprise features off; it never breaks the core platform.
- **Soft enforcement, by decision.** A fork can stub `EntitlementService` to
  always-True. The moat is the commercial license, signed releases, and
  support — not code obfuscation.

### Tiering direction (planning, not implemented)

The research doc sketches a future **Community / Team / Enterprise** tiering
(Community = full single-owner fleet + at least one free channel; Team =
collaboration/sharing/all channels; Enterprise = compliance/governance/scale).
**Today none of that gating exists** — the only live split is the binary one
in the table above, and the features sketched for "Team" are currently all OSS.

### Licensing status

The OSS repo's license-of-record is currently unresolved (`NOASSERTION`);
conversion to Apache 2.0 is tracked in
[#1139](https://github.com/abilityai/trinity/issues/1139) and is a
prerequisite for the Phase 1 license mechanism (#1040). The private submodule
carries a proprietary LICENSE.
