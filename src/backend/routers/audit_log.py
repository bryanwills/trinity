"""
Platform Audit Log API (SEC-001 / Issue #20 — Phase 1).

Admin-only query interface over the platform `audit_log` table. Phase 1 ships
read endpoints; integration write paths land in Phase 2 (lifecycle, auth,
sharing, settings, credentials), Phase 3 (MCP tools), and Phase 4 (hash-chain
verification, export).

Mounted at `/api/audit-log` rather than `/api/audit` to coexist with the
existing Process Engine audit router (`routers/audit.py`) without breaking
URL contracts. A unified surface can be added later.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from database import db
from dependencies import require_admin
from models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audit-log", tags=["audit-log"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AuditLogEntry(BaseModel):
    """Single audit log row as returned to API clients."""

    id: int
    event_id: str
    event_type: str
    event_action: str
    actor_type: str
    actor_id: Optional[str] = None
    actor_email: Optional[str] = None
    actor_ip: Optional[str] = None
    mcp_key_id: Optional[str] = None
    mcp_key_name: Optional[str] = None
    mcp_scope: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    timestamp: str
    details: Optional[dict] = None
    request_id: Optional[str] = None
    source: str
    endpoint: Optional[str] = None
    previous_hash: Optional[str] = None
    entry_hash: Optional[str] = None
    created_at: Optional[str] = None


class AuditLogListResponse(BaseModel):
    """Paginated list response."""

    entries: List[AuditLogEntry]
    total: int
    limit: int
    offset: int


class AuditLogStatsResponse(BaseModel):
    """Aggregate counts."""

    total: int
    by_event_type: dict = Field(default_factory=dict)
    by_actor_type: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Endpoints — all admin-only
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=AuditLogStatsResponse)
async def audit_log_stats(
    start_time: Optional[str] = Query(None, description="ISO 8601 UTC inclusive lower bound"),
    end_time: Optional[str] = Query(None, description="ISO 8601 UTC inclusive upper bound"),
    _admin: User = Depends(require_admin),
):
    """Aggregate counts by event_type and actor_type for the time window."""
    stats = db.get_audit_stats(start_time=start_time, end_time=end_time)
    return AuditLogStatsResponse(**stats)


@router.get("", response_model=AuditLogListResponse)
async def list_audit_log(
    event_type: Optional[str] = Query(None, description="Filter by event_type (e.g. agent_lifecycle)"),
    actor_type: Optional[str] = Query(None, description="Filter by actor_type (user/agent/mcp_client/system)"),
    actor_id: Optional[str] = Query(None, description="Filter by actor_id (user.id or agent_name)"),
    target_type: Optional[str] = Query(None, description="Filter by target_type"),
    target_id: Optional[str] = Query(None, description="Filter by target_id"),
    source: Optional[str] = Query(None, description="Filter by source (api/mcp/scheduler/system)"),
    start_time: Optional[str] = Query(None, description="ISO 8601 UTC inclusive lower bound"),
    end_time: Optional[str] = Query(None, description="ISO 8601 UTC inclusive upper bound"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _admin: User = Depends(require_admin),
):
    """List audit entries newest-first with optional filters and pagination."""
    filters = {
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "target_type": target_type,
        "target_id": target_id,
        "source": source,
        "start_time": start_time,
        "end_time": end_time,
    }
    entries = db.get_audit_entries(limit=limit, offset=offset, **filters)
    total = db.count_audit_entries(**filters)
    return AuditLogListResponse(
        entries=[AuditLogEntry(**e) for e in entries],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{event_id}", response_model=AuditLogEntry)
async def get_audit_log_entry(
    event_id: str,
    _admin: User = Depends(require_admin),
):
    """Look up a single audit entry by its UUID event_id."""
    entry = db.get_audit_entry(event_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    return AuditLogEntry(**entry)
