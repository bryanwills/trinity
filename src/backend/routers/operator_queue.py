"""
Operator Queue API Router (OPS-001).

REST API for the Operating Room — lists queue items, submits responses,
and provides statistics. Items are synced from agent JSON files by the
operator_queue_service background poller.
"""

import json
from typing import Optional, Set
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from database import db
from dependencies import get_current_user
from db_models import User


router = APIRouter(prefix="/api/operator-queue", tags=["operator-queue"])

# WebSocket manager injected from main.py
_websocket_manager = None


def set_websocket_manager(manager):
    """Set the WebSocket manager for broadcasting events."""
    global _websocket_manager
    _websocket_manager = manager


# ============================================================================
# Request/Response Models
# ============================================================================

class OperatorResponse(BaseModel):
    """Body for responding to a queue item."""
    response: str
    response_text: Optional[str] = None


# ============================================================================
# Access control helper
# ============================================================================

def _accessible_set(current_user: User) -> Optional[Set[str]]:
    """Return the set of agent names the user may access in the operator queue.

    Returns None for admins (no filter — sees everything).
    Returns a set (possibly empty) for regular users.
    """
    if current_user.role == "admin":
        return None
    user_email = current_user.email or ""
    names = db.get_accessible_agent_names(user_email, is_admin=False)
    return set(names)


def _assert_agent_accessible(agent_name: str, accessible: Optional[Set[str]]) -> None:
    """Raise 403 if the user cannot access the given agent."""
    if accessible is not None and agent_name not in accessible:
        raise HTTPException(status_code=403, detail="Access denied")


# ============================================================================
# Endpoints
# ============================================================================

@router.get("")
async def list_queue_items(
    status: Optional[str] = Query(None, description="Filter by status"),
    type: Optional[str] = Query(None, description="Filter by type"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    agent_name: Optional[str] = Query(None, description="Filter by agent"),
    since: Optional[str] = Query(None, description="Items created after this ISO timestamp"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
):
    """List operator queue items with optional filters."""
    accessible = _accessible_set(current_user)
    items = db.list_operator_queue_items(
        status=status,
        type=type,
        priority=priority,
        agent_name=agent_name,
        since=since,
        limit=limit,
        offset=offset,
        accessible_agent_names=accessible,
    )
    return {"items": items, "count": len(items)}


@router.get("/stats")
async def get_queue_stats(
    current_user: User = Depends(get_current_user),
):
    """Get queue statistics (counts by status, type, priority, agent)."""
    accessible = _accessible_set(current_user)
    return db.get_operator_queue_stats(accessible_agent_names=accessible)


@router.get("/{item_id}")
async def get_queue_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a single queue item by ID."""
    item = db.get_operator_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    accessible = _accessible_set(current_user)
    _assert_agent_accessible(item["agent_name"], accessible)
    return item


@router.post("/{item_id}/respond")
async def respond_to_queue_item(
    item_id: str,
    body: OperatorResponse,
    current_user: User = Depends(get_current_user),
):
    """Submit an operator response to a pending queue item."""
    existing = db.get_operator_queue_item(item_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Queue item not found")

    accessible = _accessible_set(current_user)
    _assert_agent_accessible(existing["agent_name"], accessible)

    if existing["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot respond to item with status '{existing['status']}'"
        )

    item = db.respond_to_operator_queue_item(
        item_id=item_id,
        response=body.response,
        response_text=body.response_text,
        responded_by_id=str(current_user.id),
        responded_by_email=current_user.email or current_user.username,
    )

    # Broadcast WebSocket event
    if _websocket_manager and item:
        await _websocket_manager.broadcast(json.dumps({
            "type": "operator_queue_responded",
            "data": {
                "id": item_id,
                "agent_name": item["agent_name"],
                "responded_by_email": current_user.email or current_user.username,
                "response": body.response,
            }
        }))

    return item


@router.post("/{item_id}/cancel")
async def cancel_queue_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
):
    """Cancel a pending queue item."""
    existing = db.get_operator_queue_item(item_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Queue item not found")

    accessible = _accessible_set(current_user)
    _assert_agent_accessible(existing["agent_name"], accessible)

    if existing["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel item with status '{existing['status']}'"
        )

    item = db.cancel_operator_queue_item(item_id)
    return item


@router.get("/agents/{agent_name}")
async def get_agent_queue_items(
    agent_name: str,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
):
    """Get queue items for a specific agent."""
    accessible = _accessible_set(current_user)
    _assert_agent_accessible(agent_name, accessible)
    items = db.list_operator_queue_items(
        agent_name=agent_name,
        status=status,
        limit=limit,
    )
    return {"agent_name": agent_name, "items": items, "count": len(items)}
