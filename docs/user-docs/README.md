# Trinity User Documentation

> Auto-generated from source code. Run `/generate-user-docs` to update.

## Getting Started

- [Overview](getting-started/overview.md) — What is Trinity, key concepts, architecture
- [Setup](getting-started/setup.md) — Installation, first-time setup, login
- [Quick Start](getting-started/quick-start.md) — Create your first agent in 5 minutes

## Agents

- [Creating Agents](agents/creating-agents.md) — Templates, GitHub repos, from scratch
- [Managing Agents](agents/managing-agents.md) — Start/stop, rename, delete, health
- [Agent Chat](agents/agent-chat.md) — Chat interface, voice, streaming, history
- [Agent Terminal](agents/agent-terminal.md) — Web terminal, SSH access, mode switching
- [Agent Files](agents/agent-files.md) — File browser, virtual filesystem, shared folders
- [Agent Logs](agents/agent-logs.md) — Log viewing, telemetry, Vector aggregation
- [Agent Configuration](agents/agent-configuration.md) — Autonomy, read-only, resources, timeout

## Credentials

- [Credential Management](credentials/credential-management.md) — Adding, editing, hot-reload, encrypted backup
- [OAuth Credentials](credentials/oauth-credentials.md) — OAuth2 flows for Google, Slack, GitHub, Notion
- [Subscription Credentials](credentials/subscription-credentials.md) — Shared Claude subscriptions, auto-assign, auto-switch

## Collaboration

- [Agent Network](collaboration/agent-network.md) — Multi-agent communication, async collaboration, DAG visualization
- [Agent Permissions](collaboration/agent-permissions.md) — Who can call whom, access control
- [Event Subscriptions](collaboration/event-subscriptions.md) — Pub/sub between agents, message templates
- [System Manifest](collaboration/system-manifest.md) — Recipe-based multi-agent deployment

## Automation

- [Scheduling](automation/scheduling.md) — Cron schedules, execution queue, misfire handling
- [Skills and Playbooks](automation/skills-and-playbooks.md) — Skills library, assignment, chat autocomplete
- [Approvals](automation/approvals.md) — Human-in-the-loop approval gates

## Operations

- [Dashboard](operations/dashboard.md) — Network graph, timeline view, tag clouds
- [Operating Room](operations/operating-room.md) — Operator queue, notifications, cost alerts
- [Monitoring](operations/monitoring.md) — Health checks, cleanup service, fleet dashboard
- [Executions](operations/executions.md) — Execution list, detail, live streaming, termination

## Sharing and Access

- [Agent Sharing](sharing-and-access/agent-sharing.md) — Share with users, access levels
- [Public Links](sharing-and-access/public-links.md) — Public chat URLs, email verification, session memory
- [Tags and Organization](sharing-and-access/tags-and-organization.md) — Tags, filtering, system views
- [Mobile Admin](sharing-and-access/mobile-admin.md) — Mobile PWA at /m

## Integrations

- [GitHub PAT Setup](integrations/github-pat-setup.md) — Personal Access Token configuration for GitHub features
- [GitHub Sync](integrations/github-sync.md) — Source mode, working branch mode, branch selection
- [Slack Integration](integrations/slack-integration.md) — Multi-agent channels, DMs, thread routing
- [Telegram Integration](integrations/telegram-integration.md) — Bot setup, group chats, privacy mode, trigger modes
- [MCP Server](integrations/mcp-server.md) — 62 MCP tools, API keys, tool categories
- [Nevermined Payments](integrations/nevermined-payments.md) — x402 payment monetization

## Dev Announcements

- [Dev Announcements](dev-announcements/) — Timestamped archive of all `/announce` messages sent to Discord and Slack

## Advanced

- [Voice Chat](advanced/voice-chat.md) — Real-time voice via Gemini Live API
- [Image Generation](advanced/image-generation.md) — Gemini two-step image pipeline
- [Agent Avatars](advanced/agent-avatars.md) — AI-generated avatars, emotion variants
- [Process Engine](advanced/process-engine.md) — BPMN workflows, step types, analytics
- [Dynamic Dashboards](advanced/dynamic-dashboards.md) — Custom agent dashboards via YAML

## API Reference

- [Authentication](api-reference/authentication.md) — JWT tokens, API keys, auth flows
- [Agent API](api-reference/agent-api.md) — Agent CRUD, lifecycle, configuration endpoints
- [Chat API](api-reference/chat-api.md) — Chat, voice, streaming, public/paid endpoints
- [Webhook Triggers](api-reference/webhook-triggers.md) — Internal triggers, event webhooks
