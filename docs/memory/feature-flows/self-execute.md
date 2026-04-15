# Self-Execute: Agent Background Task During Chat (SELF-EXEC-001)

## Overview

**Feature ID:** SELF-EXEC-001
**GitHub Issue:** #264
**Status:** Implemented (2026-04-14)

Allow an agent to trigger a background task execution on itself while actively chatting with a user. The agent calls `chat_with_agent(agent_name=<self>, parallel=true)` via MCP, and Trinity tracks this as a `SELF_TASK` activity with optional result injection back into the chat.

## User Story

As an agent, I want to kick off background work (research, file processing, code generation) while keeping my chat session responsive, so I can tell the user "I'm working on that in the background" and they see the progress in the activity panel.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Self-Execute Flow                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Agent (in chat) ──► MCP chat_with_agent(self, inject_result=true)   │
│         │                                                             │
│         ▼                                                             │
│  MCP Server ──► Detects self-call ──► Logs [Self-Task]               │
│         │                                                             │
│         ▼                                                             │
│  Backend /task ──► Validates X-Source-Agent matches MCP key scope    │
│         │                                                             │
│         ├──► is_self_task = (x_source_agent == name)                 │
│         ├──► Creates execution record                                │
│         ├──► Tracks SELF_TASK activity (not AGENT_COLLABORATION)     │
│         └──► TaskExecutionService.execute_task_async()               │
│                    │                                                  │
│                    ▼                                                  │
│              On completion ──► If inject_result && chat_session_id:  │
│                    │           db.add_chat_message(source='self_task')│
│                    │           broadcast WebSocket event              │
│                    ▼                                                  │
│              Frontend ──► Activity panel shows self-task             │
│                       ──► Chat receives injected result (collapsed)  │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Components

### Backend

| File | Lines | Description |
|------|-------|-------------|
| `src/backend/models.py` | 115-118 | `ActivityType.SELF_TASK` enum value |
| `src/backend/models.py` | 96 | `inject_result` param on `ParallelTaskRequest` |
| `src/backend/routers/chat.py` | 664-674 | Security validation + self-task detection |
| `src/backend/routers/chat.py` | 697-744 | SELF_TASK activity tracking and WebSocket broadcast |
| `src/backend/routers/chat.py` | 625-690 | Result injection on task completion |

### MCP Server

| File | Lines | Description |
|------|-------|-------------|
| `src/mcp-server/src/tools/chat.ts` | 195-210 | `inject_result` and `chat_session_id` params |
| `src/mcp-server/src/tools/chat.ts` | 258-265 | Self-call detection and logging |
| `src/mcp-server/src/client.ts` | 520-522 | Options interface with new params |
| `src/mcp-server/src/client.ts` | 555-558 | Pass params in request body |

### Frontend

| File | Lines | Description |
|------|-------|-------------|
| `src/frontend/src/stores/network.js` | 234 | `self_task` in activity_types query |
| `src/frontend/src/components/chat/ChatBubble.vue` | 17-52 | Self-task result rendering (collapsible) |

## API

### MCP Tool: chat_with_agent

New parameters for self-execute:

```typescript
{
  agent_name: string,     // Must equal calling agent's name for self-task
  message: string,
  parallel: true,         // Required for self-task
  inject_result?: boolean,  // If true, inject result into chat session
  chat_session_id?: string, // Session to inject result into
  async?: boolean         // Recommended true for background work
}
```

### Backend: POST /api/agents/{name}/task

New request body fields:

```json
{
  "message": "Research competitor pricing",
  "async_mode": true,
  "inject_result": true,
  "chat_session_id": "session-abc-123"
}
```

### WebSocket Events

**Self-task started:**
```json
{
  "type": "agent_activity",
  "agent_name": "my-agent",
  "activity_type": "self_task",
  "activity_state": "started",
  "action": "Background task: Research competitor...",
  "details": {
    "execution_id": "exec-123",
    "chat_session_id": "session-abc",
    "inject_result": true
  }
}
```

**Self-task completed:**
```json
{
  "type": "agent_activity",
  "agent_name": "my-agent",
  "activity_type": "self_task",
  "activity_state": "completed",
  "details": {
    "execution_id": "exec-123",
    "cost_usd": 0.05,
    "execution_time_ms": 45000,
    "response_preview": "Found 3 competitors...",
    "result_injected": true
  }
}
```

## Security

### Header Validation

The backend validates that the `X-Source-Agent` header matches the MCP key's agent scope to prevent header spoofing:

```python
if x_source_agent and current_user.agent_name:
    if x_source_agent != current_user.agent_name:
        raise HTTPException(403, "Source agent header doesn't match API key scope")
```

### Session Ownership

Before injecting a result into a chat session, the backend validates ownership:

```python
session = db.get_chat_session(request.chat_session_id)
if session and session.get("user_id") == user_id:
    # Inject result
else:
    logger.warning("Cannot inject: session not owned by user")
```

## UI Behavior

### Activity Panel

Self-task activities appear in the Network view activity timeline with:
- Activity type: `self_task`
- Triggered by: `self_task`
- Agent name: The agent that called itself

### Chat Bubble

Self-task results in chat have distinct styling:
- Purple background (`bg-purple-50 dark:bg-purple-900/20`)
- Header: "Background Task Result" with refresh icon
- Collapsed by default (click to expand)
- Shows preview when collapsed

## Database

### chat_messages.source Column

Self-task results are stored with `source='self_task'`:

```sql
INSERT INTO chat_messages (..., source, ...)
VALUES (..., 'self_task', ...)
```

This allows frontend to render them differently from regular assistant messages.

## Testing

Test file: `tests/test_self_execute.py`

| Test Class | Coverage |
|------------|----------|
| `TestSelfTaskActivityType` | SELF_TASK enum exists and is defined |
| `TestParallelTaskRequestModel` | inject_result, chat_session_id params |
| `TestSelfTaskDetection` | Detection logic when source == target |
| `TestSourceAgentHeaderValidation` | Security validation of headers |
| `TestTriggeredByField` | triggered_by values for different call types |
| `TestChatSessionValidation` | Session ownership validation |
| `TestInjectResultConditions` | Conditions for result injection |

## Usage Example

Agent code to run background task on itself:

```python
# In agent's CLAUDE.md or skill
result = await mcp.chat_with_agent(
    agent_name="my-agent",  # Same as calling agent
    message="Research the top 5 competitors and create a comparison table",
    parallel=True,
    async=True,
    inject_result=True,
    chat_session_id=current_session_id
)
# Returns immediately with execution_id
# Result will appear in chat when task completes
```

## Related Features

- **Parallel Headless Execution** (`parallel-headless-execution.md`) — Base parallel task infrastructure
- **Task Execution Service** (`task-execution-service.md`) — Unified execution lifecycle
- **Persistent Chat Tracking** (`persistent-chat-tracking.md`) — Chat message persistence
- **Activity Stream** (`activity-stream.md`) — Activity tracking and WebSocket events

## Out of Scope

- **Cancellation UI** — Future enhancement to cancel running self-tasks from chat
- **Progress streaming** — Real-time progress updates from self-task to chat
- **Chaining** — One self-task triggering another (already works via existing MCP)
