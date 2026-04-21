"""
SUB-003 ping-pong prevention tests (issue #444).

Before the fix, `_perform_auto_switch()` called `clear_rate_limit_events()`
after every successful switch, deleting the per-(agent, subscription) events
that are the detection signal for `is_subscription_rate_limited()`. Once
deleted, the just-drained subscription looked viable again, causing agents to
ping-pong between two exhausted subscriptions on every subsequent 429.

These tests pin the fix at the db layer: after a simulated switch, the old
subscription must still be reported as rate-limited, and
`select_best_alternative_subscription()` must return None when every candidate
has rate-limit events in the 2h window.
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make src/backend importable and evict any shadow `utils` package that the
# parent tests/ directory would otherwise resolve to (mirrors test_backlog.py).
_THIS = Path(__file__).resolve()
_BACKEND = _THIS.parent.parent.parent / "src" / "backend"
_BACKEND_STR = str(_BACKEND)
for _shadow in ("utils", "utils.api_client", "utils.assertions", "utils.cleanup"):
    sys.modules.pop(_shadow, None)
while _BACKEND_STR in sys.path:
    sys.path.remove(_BACKEND_STR)
sys.path.insert(0, _BACKEND_STR)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Provision a fresh SQLite DB with the tables SUB-003 touches.

    Only columns read/written by SubscriptionOperations are created — this keeps
    the test isolated from schema drift elsewhere.
    """
    db_path = tmp_path / "trinity.db"
    monkeypatch.setenv("TRINITY_DB_PATH", str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE subscription_credentials (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            encrypted_credentials TEXT NOT NULL,
            subscription_type TEXT,
            rate_limit_tier TEXT,
            owner_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE agent_ownership (
            agent_name TEXT PRIMARY KEY,
            owner_id INTEGER,
            subscription_id TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE subscription_rate_limit_events (
            id TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            subscription_id TEXT NOT NULL,
            error_message TEXT,
            occurred_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX idx_rate_limit_agent_sub "
        "ON subscription_rate_limit_events(agent_name, subscription_id, occurred_at DESC)"
    )
    cur.execute(
        "CREATE INDEX idx_rate_limit_sub "
        "ON subscription_rate_limit_events(subscription_id, occurred_at DESC)"
    )

    # Seed: 1 user, 2 subscriptions, 1 agent assigned to sub-A
    now = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO users (id, username, email, role, created_at, updated_at) "
        "VALUES (1, 'tester', 'tester@example.com', 'admin', ?, ?)",
        (now, now),
    )
    cur.execute(
        "INSERT INTO subscription_credentials "
        "(id, name, encrypted_credentials, owner_id, created_at, updated_at) "
        "VALUES ('sub-a', 'sub-A', 'enc-a', 1, ?, ?)",
        (now, now),
    )
    cur.execute(
        "INSERT INTO subscription_credentials "
        "(id, name, encrypted_credentials, owner_id, created_at, updated_at) "
        "VALUES ('sub-b', 'sub-B', 'enc-b', 1, ?, ?)",
        (now, now),
    )
    cur.execute(
        "INSERT INTO agent_ownership (agent_name, owner_id, subscription_id) "
        "VALUES ('agent-x', 1, 'sub-a')"
    )
    conn.commit()
    conn.close()

    # Force re-import so the module-level DB_PATH picks up our env var.
    for mod in ("db.connection", "db.subscriptions"):
        sys.modules.pop(mod, None)

    yield db_path


@pytest.fixture
def sub_ops(tmp_db):
    """Fresh SubscriptionOperations bound to tmp_db with a stub encryption service."""
    from db.subscriptions import SubscriptionOperations

    # Encryption service is only used by create_subscription / get_subscription_token,
    # which these tests don't exercise. A stub keeps us off the real service.
    return SubscriptionOperations(encryption_service=MagicMock())


def _record_events(sub_ops, agent_name: str, subscription_id: str, count: int) -> int:
    last = 0
    for _ in range(count):
        last = sub_ops.record_rate_limit_event(
            agent_name=agent_name,
            subscription_id=subscription_id,
            error_message="Subscription usage limit: You've hit your limit",
        )
    return last


class TestPingPongPrevention:
    """SUB-003 regression tests for issue #444."""

    def test_old_subscription_stays_rate_limited_after_switch(self, sub_ops):
        """After a switch, the old sub's events must persist so `is_subscription_rate_limited`
        continues to flag it — this is what stops the ping-pong on the next cycle."""
        # Simulate 2 consecutive 429s on sub-A → triggers switch
        count = _record_events(sub_ops, "agent-x", "sub-a", 2)
        assert count == 2
        assert sub_ops.is_subscription_rate_limited("sub-a") is True

        # Simulate _perform_auto_switch doing its work WITHOUT calling
        # clear_rate_limit_events (post-fix behavior).
        sub_ops.assign_subscription_to_agent("agent-x", "sub-b")

        # Signal must survive — this is the fix.
        assert sub_ops.is_subscription_rate_limited("sub-a") is True

    def test_no_alternative_when_both_subs_exhausted(self, sub_ops):
        """Given two subscriptions that have each hit the limit,
        select_best_alternative_subscription must return None — not pick the
        other exhausted sub."""
        _record_events(sub_ops, "agent-x", "sub-a", 2)
        _record_events(sub_ops, "agent-x", "sub-b", 2)

        # Agent currently on sub-A → asking for an alternative to sub-A
        assert sub_ops.select_best_alternative_subscription("sub-a") is None
        # Symmetric: from sub-B's perspective too
        assert sub_ops.select_best_alternative_subscription("sub-b") is None

    def test_pingpong_blocked_across_two_switches(self, sub_ops):
        """Full ping-pong scenario: both subscriptions have 429s recorded. After
        the first switch (A→B), the second check (from B) must refuse to switch
        back to A because A is still flagged as rate-limited."""
        # First cycle: agent-x on sub-A, 2× 429
        _record_events(sub_ops, "agent-x", "sub-a", 2)
        # Auto-switch picks sub-B (the only other sub, not yet flagged)
        alt1 = sub_ops.select_best_alternative_subscription("sub-a")
        assert alt1 is not None
        assert alt1.id == "sub-b"
        # Perform the switch (post-fix: no clear)
        sub_ops.assign_subscription_to_agent("agent-x", "sub-b")

        # Second cycle: 2× 429 on sub-B too
        _record_events(sub_ops, "agent-x", "sub-b", 2)
        # sub-A still rate-limited → no viable alternative → no ping-pong back
        alt2 = sub_ops.select_best_alternative_subscription("sub-b")
        assert alt2 is None

    def test_viable_alternative_found_when_only_one_sub_exhausted(self, sub_ops):
        """Sanity check: if only one subscription is rate-limited, the other is
        still a valid alternative (the fix must not over-correct and refuse all
        switches)."""
        _record_events(sub_ops, "agent-x", "sub-a", 2)
        alt = sub_ops.select_best_alternative_subscription("sub-a")
        assert alt is not None
        assert alt.id == "sub-b"
