"""
Watchdog Unit Tests (test_watchdog_unit.py)

Unit tests for Issue #129: Active watchdog reconciliation logic.
Tests DB methods, reconciliation decision matrix, recovery helper,
and error isolation — all with mocked agent HTTP responses.
"""

import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

# Add backend to path for direct imports in unit tests
_backend_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "backend")
)
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)

# Pre-mock modules that conflict with test environment:
# - database: tries to write to /data (doesn't exist outside Docker)
# - utils.helpers: shadowed by tests/utils/ package
# - models: depends on utils.helpers
from unittest.mock import MagicMock as _MagicMock

# tests/utils shadows src/backend/utils — provide real helper implementations
# needed by cleanup_service for timestamp math
import types as _types
_helpers_mod = _types.ModuleType("utils.helpers")

def _utc_now():
    return datetime.utcnow()

def _utc_now_iso():
    return datetime.utcnow().isoformat() + "Z"

def _parse_iso_timestamp(s):
    s = s.rstrip("Z")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.utcnow()

_helpers_mod.utc_now = _utc_now
_helpers_mod.utc_now_iso = _utc_now_iso
_helpers_mod.parse_iso_timestamp = _parse_iso_timestamp
_helpers_mod.to_utc_iso = _MagicMock(return_value="2025-01-01T00:00:00Z")
sys.modules["utils.helpers"] = _helpers_mod

sys.modules.setdefault("database", _MagicMock())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso():
    return datetime.utcnow().isoformat() + "Z"


def _past_iso(minutes: int) -> str:
    """Return an ISO timestamp N minutes in the past."""
    return (datetime.utcnow() - timedelta(minutes=minutes)).isoformat() + "Z"


# ---------------------------------------------------------------------------
# CleanupReport tests
# ---------------------------------------------------------------------------

class TestCleanupReport:
    """Tests for expanded CleanupReport dataclass."""

    pytestmark = pytest.mark.unit

    def test_report_includes_watchdog_fields(self):
        """CleanupReport has orphaned_executions and auto_terminated fields."""
        from services.cleanup_service import CleanupReport

        report = CleanupReport()
        assert report.orphaned_executions == 0
        assert report.auto_terminated == 0

    def test_report_total_includes_watchdog_fields(self):
        """Total correctly sums all fields including watchdog additions."""
        import sys
        import os
        backend_path = os.path.join(os.path.dirname(__file__), "..", "src", "backend")
        if backend_path not in sys.path:
            sys.path.insert(0, os.path.abspath(backend_path))

        from services.cleanup_service import CleanupReport

        report = CleanupReport(
            orphaned_executions=2,
            auto_terminated=1,
            stale_executions=3,
            no_session_executions=1,
            orphaned_skipped=0,
            stale_activities=1,
            stale_slots=0,
        )
        assert report.total == 8

    def test_report_to_dict_includes_watchdog_fields(self):
        """to_dict() includes watchdog fields."""
        import sys
        import os
        backend_path = os.path.join(os.path.dirname(__file__), "..", "src", "backend")
        if backend_path not in sys.path:
            sys.path.insert(0, os.path.abspath(backend_path))

        from services.cleanup_service import CleanupReport

        report = CleanupReport(orphaned_executions=1, auto_terminated=2)
        d = report.to_dict()
        assert d["orphaned_executions"] == 1
        assert d["auto_terminated"] == 2
        assert "total" in d


# ---------------------------------------------------------------------------
# DB method tests (using in-memory SQLite)
# ---------------------------------------------------------------------------

class TestGetRunningExecutionsWithAgentInfo:
    """Tests for get_running_executions_with_agent_info() DB method."""

    pytestmark = pytest.mark.unit

    def _setup_db(self):
        """Create in-memory SQLite with required tables and return connection.

        Includes agent_ownership table to match the production 3-way COALESCE
        query: COALESCE(s.timeout_seconds, ao.execution_timeout_seconds, 900).
        """
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE agent_schedules (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                name TEXT NOT NULL,
                cron_expression TEXT NOT NULL,
                message TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                timezone TEXT DEFAULT 'UTC',
                description TEXT,
                owner_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                timeout_seconds INTEGER DEFAULT 900
            )
        """)
        conn.execute("""
            CREATE TABLE schedule_executions (
                id TEXT PRIMARY KEY,
                schedule_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_ms INTEGER,
                message TEXT NOT NULL,
                response TEXT,
                error TEXT,
                triggered_by TEXT NOT NULL DEFAULT 'schedule',
                claude_session_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE agent_ownership (
                agent_name TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                execution_timeout_seconds INTEGER DEFAULT 900
            )
        """)
        conn.commit()
        return conn

    def test_returns_running_executions_with_timeout(self):
        """Returns running executions joined with schedule timeout."""
        conn = self._setup_db()
        conn.execute(
            "INSERT INTO agent_schedules (id, agent_name, name, cron_expression, message, owner_id, created_at, updated_at, timeout_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sched-1", "agent-a", "Test Schedule", "0 * * * *", "do something", 1, _utc_now_iso(), _utc_now_iso(), 600),
        )
        conn.execute(
            "INSERT INTO schedule_executions (id, schedule_id, agent_name, status, started_at, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("exec-1", "sched-1", "agent-a", "running", _past_iso(10), "test message"),
        )
        conn.commit()

        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.id, e.schedule_id, e.agent_name, e.started_at, e.message,
                   COALESCE(s.timeout_seconds, ao.execution_timeout_seconds, 900) as timeout_seconds
            FROM schedule_executions e
            LEFT JOIN agent_schedules s ON e.schedule_id = s.id
            LEFT JOIN agent_ownership ao ON e.agent_name = ao.agent_name
            WHERE e.status = 'running'
        """)
        rows = [dict(r) for r in cursor.fetchall()]

        assert len(rows) == 1
        assert rows[0]["id"] == "exec-1"
        assert rows[0]["agent_name"] == "agent-a"
        assert rows[0]["timeout_seconds"] == 600

    def test_manual_execution_coalesces_to_default(self):
        """Manual executions (no schedule) get COALESCE default of 900s."""
        conn = self._setup_db()
        conn.execute(
            "INSERT INTO schedule_executions (id, schedule_id, agent_name, status, started_at, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("exec-2", "__manual__", "agent-b", "running", _past_iso(5), "manual task"),
        )
        conn.commit()

        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.id, COALESCE(s.timeout_seconds, ao.execution_timeout_seconds, 900) as timeout_seconds
            FROM schedule_executions e
            LEFT JOIN agent_schedules s ON e.schedule_id = s.id
            LEFT JOIN agent_ownership ao ON e.agent_name = ao.agent_name
            WHERE e.status = 'running'
        """)
        rows = [dict(r) for r in cursor.fetchall()]

        assert len(rows) == 1
        assert rows[0]["timeout_seconds"] == 900

    def test_agent_timeout_fallback(self):
        """When schedule has no timeout but agent_ownership does, use agent timeout."""
        conn = self._setup_db()
        # Schedule with NULL timeout
        conn.execute(
            "INSERT INTO agent_schedules (id, agent_name, name, cron_expression, message, owner_id, created_at, updated_at, timeout_seconds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sched-1", "agent-a", "Test Schedule", "0 * * * *", "do something", 1, _utc_now_iso(), _utc_now_iso(), None),
        )
        # Agent ownership with custom timeout
        conn.execute(
            "INSERT INTO agent_ownership (agent_name, owner_id, execution_timeout_seconds) "
            "VALUES (?, ?, ?)",
            ("agent-a", 1, 1800),
        )
        conn.execute(
            "INSERT INTO schedule_executions (id, schedule_id, agent_name, status, started_at, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("exec-1", "sched-1", "agent-a", "running", _past_iso(10), "test"),
        )
        conn.commit()

        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.id, COALESCE(s.timeout_seconds, ao.execution_timeout_seconds, 900) as timeout_seconds
            FROM schedule_executions e
            LEFT JOIN agent_schedules s ON e.schedule_id = s.id
            LEFT JOIN agent_ownership ao ON e.agent_name = ao.agent_name
            WHERE e.status = 'running'
        """)
        rows = [dict(r) for r in cursor.fetchall()]

        assert len(rows) == 1
        assert rows[0]["timeout_seconds"] == 1800

    def test_empty_result_when_no_running(self):
        """Returns empty list when no running executions."""
        conn = self._setup_db()
        conn.execute(
            "INSERT INTO schedule_executions (id, schedule_id, agent_name, status, started_at, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("exec-3", "__manual__", "agent-c", "success", _past_iso(60), "done"),
        )
        conn.commit()

        cursor = conn.cursor()
        cursor.execute("""
            SELECT e.id FROM schedule_executions e WHERE e.status = 'running'
        """)
        rows = cursor.fetchall()
        assert len(rows) == 0


class TestMarkExecutionFailedByWatchdog:
    """Tests for mark_execution_failed_by_watchdog() DB method."""

    pytestmark = pytest.mark.unit

    def _setup_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE schedule_executions (
                id TEXT PRIMARY KEY,
                schedule_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_ms INTEGER,
                message TEXT NOT NULL,
                error TEXT
            )
        """)
        conn.commit()
        return conn

    def test_marks_running_as_failed(self):
        """Updates status from running to failed with error message."""
        conn = self._setup_db()
        started = _past_iso(20)
        conn.execute(
            "INSERT INTO schedule_executions (id, schedule_id, agent_name, status, started_at, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("exec-1", "sched-1", "agent-a", "running", started, "test"),
        )
        conn.commit()

        # Simulate the conditional update
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE schedule_executions
            SET status = 'failed', error = ?
            WHERE id = ? AND status = 'running'
        """, ("Recovered by watchdog", "exec-1"))
        conn.commit()

        assert cursor.rowcount == 1

        # Verify the update
        cursor.execute("SELECT status, error FROM schedule_executions WHERE id = ?", ("exec-1",))
        row = dict(cursor.fetchone())
        assert row["status"] == "failed"
        assert row["error"] == "Recovered by watchdog"

    def test_race_guard_returns_zero_if_already_completed(self):
        """WHERE status='running' guard prevents overwriting completed execution."""
        conn = self._setup_db()
        conn.execute(
            "INSERT INTO schedule_executions (id, schedule_id, agent_name, status, started_at, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("exec-2", "sched-1", "agent-a", "success", _past_iso(20), "test"),
        )
        conn.commit()

        cursor = conn.cursor()
        cursor.execute("""
            UPDATE schedule_executions
            SET status = 'failed', error = ?
            WHERE id = ? AND status = 'running'
        """, ("Recovered by watchdog", "exec-2"))
        conn.commit()

        assert cursor.rowcount == 0  # No rows updated — already completed


# ---------------------------------------------------------------------------
# Reconciliation logic tests
# ---------------------------------------------------------------------------

class TestReconcileOrphanedExecutions:
    """Tests for _reconcile_orphaned_executions() logic."""

    pytestmark = pytest.mark.unit

    def _make_service(self):
        """Create a CleanupService with mocked dependencies."""
        from services.cleanup_service import CleanupService
        return CleanupService()

    def _mock_httpx_client(self):
        """Create a mock httpx.AsyncClient context manager."""
        mock_client = AsyncMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        return mock_cm, mock_client

    @patch("services.cleanup_service.httpx.AsyncClient")
    @patch("services.cleanup_service.db")
    @patch("services.cleanup_service.get_slot_service")
    @patch("services.cleanup_service.get_execution_queue")
    def test_agent_unreachable_skips(self, mock_queue_fn, mock_slot_fn, mock_db, mock_httpx):
        """When agent is unreachable, skip its executions entirely."""
        mock_cm, mock_client = self._mock_httpx_client()
        mock_httpx.return_value = mock_cm

        mock_db.get_running_executions_with_agent_info.return_value = [
            {"id": "exec-1", "agent_name": "agent-down", "started_at": _past_iso(60), "timeout_seconds": 900, "schedule_id": "s1"},
        ]

        service = self._make_service()

        # Mock _get_agent_running_ids to return None (unreachable)
        service._get_agent_running_ids = AsyncMock(return_value=None)

        orphaned, terminated, confirmed_running = asyncio.run(
            service._reconcile_orphaned_executions()
        )

        assert orphaned == 0
        assert terminated == 0
        assert confirmed_running == set()
        mock_db.mark_execution_failed_by_watchdog.assert_not_called()

    @patch("services.cleanup_service.httpx.AsyncClient")
    @patch("services.cleanup_service.db")
    @patch("services.cleanup_service.get_slot_service")
    @patch("services.cleanup_service.get_execution_queue")
    def test_orphan_not_found_on_agent(self, mock_queue_fn, mock_slot_fn, mock_db, mock_httpx):
        """Execution not found on agent -> orphan recovery."""
        mock_cm, _ = self._mock_httpx_client()
        mock_httpx.return_value = mock_cm

        mock_db.get_running_executions_with_agent_info.return_value = [
            {"id": "exec-1", "agent_name": "agent-a", "started_at": _past_iso(10), "timeout_seconds": 900, "schedule_id": "s1"},
        ]
        mock_db.mark_execution_failed_by_watchdog.return_value = True

        mock_slot = AsyncMock()
        mock_slot_fn.return_value = mock_slot
        mock_q = AsyncMock()
        mock_queue_fn.return_value = mock_q

        service = self._make_service()
        service._get_agent_running_ids = AsyncMock(return_value=set())
        service._broadcast_watchdog_event = AsyncMock()

        orphaned, terminated, confirmed_running = asyncio.run(
            service._reconcile_orphaned_executions()
        )

        assert orphaned == 1
        assert terminated == 0
        assert confirmed_running == set()
        mock_db.mark_execution_failed_by_watchdog.assert_called_once()
        mock_slot.release_slot.assert_called_once_with("agent-a", "exec-1")
        # Atomic conditional release — no TOCTOU race
        mock_q.force_release_if_matches.assert_called_once_with("agent-a", "exec-1")

    @patch("services.cleanup_service.httpx.AsyncClient")
    @patch("services.cleanup_service.db")
    @patch("services.cleanup_service.get_slot_service")
    @patch("services.cleanup_service.get_execution_queue")
    def test_running_under_timeout_no_action(self, mock_queue_fn, mock_slot_fn, mock_db, mock_httpx):
        """Execution running on agent under timeout -> no action."""
        mock_cm, _ = self._mock_httpx_client()
        mock_httpx.return_value = mock_cm

        mock_db.get_running_executions_with_agent_info.return_value = [
            {"id": "exec-1", "agent_name": "agent-a", "started_at": _past_iso(5), "timeout_seconds": 900, "schedule_id": "s1"},
        ]

        service = self._make_service()
        service._get_agent_running_ids = AsyncMock(return_value={"exec-1"})

        orphaned, terminated, confirmed_running = asyncio.run(
            service._reconcile_orphaned_executions()
        )

        assert orphaned == 0
        assert terminated == 0
        # #226: Execution confirmed as still running within timeout
        assert confirmed_running == {"exec-1"}
        mock_db.mark_execution_failed_by_watchdog.assert_not_called()

    @patch("services.cleanup_service.httpx.AsyncClient")
    @patch("services.cleanup_service.db")
    @patch("services.cleanup_service.get_slot_service")
    @patch("services.cleanup_service.get_execution_queue")
    def test_running_over_timeout_auto_terminates(self, mock_queue_fn, mock_slot_fn, mock_db, mock_httpx):
        """Execution running on agent over timeout -> auto-terminate."""
        mock_cm, _ = self._mock_httpx_client()
        mock_httpx.return_value = mock_cm

        mock_db.get_running_executions_with_agent_info.return_value = [
            {"id": "exec-1", "agent_name": "agent-a", "started_at": _past_iso(20), "timeout_seconds": 600, "schedule_id": "s1"},
        ]
        mock_db.mark_execution_failed_by_watchdog.return_value = True

        mock_slot = AsyncMock()
        mock_slot_fn.return_value = mock_slot
        mock_q = AsyncMock()
        mock_queue_fn.return_value = mock_q

        service = self._make_service()
        service._get_agent_running_ids = AsyncMock(return_value={"exec-1"})
        service._terminate_on_agent = AsyncMock(return_value=True)
        service._broadcast_watchdog_event = AsyncMock()

        orphaned, terminated, confirmed_running = asyncio.run(
            service._reconcile_orphaned_executions()
        )

        assert orphaned == 0
        assert terminated == 1
        assert confirmed_running == set()  # Over timeout, so not confirmed
        service._terminate_on_agent.assert_called_once_with(ANY, "agent-a", "exec-1")
        mock_db.mark_execution_failed_by_watchdog.assert_called_once()

    @patch("services.cleanup_service.httpx.AsyncClient")
    @patch("services.cleanup_service.db")
    @patch("services.cleanup_service.get_slot_service")
    @patch("services.cleanup_service.get_execution_queue")
    def test_terminate_fails_skips_recovery(self, mock_queue_fn, mock_slot_fn, mock_db, mock_httpx):
        """If terminate returns False, DB/resource cleanup is skipped."""
        mock_cm, _ = self._mock_httpx_client()
        mock_httpx.return_value = mock_cm

        mock_db.get_running_executions_with_agent_info.return_value = [
            {"id": "exec-1", "agent_name": "agent-a", "started_at": _past_iso(20), "timeout_seconds": 600, "schedule_id": "s1"},
        ]

        mock_slot = AsyncMock()
        mock_slot_fn.return_value = mock_slot
        mock_q = AsyncMock()
        mock_queue_fn.return_value = mock_q

        service = self._make_service()
        service._get_agent_running_ids = AsyncMock(return_value={"exec-1"})
        service._terminate_on_agent = AsyncMock(return_value=False)
        service._broadcast_watchdog_event = AsyncMock()

        orphaned, terminated, confirmed_running = asyncio.run(
            service._reconcile_orphaned_executions()
        )

        # Terminate failed — should NOT mark as failed or release resources
        assert terminated == 0
        assert confirmed_running == set()
        mock_db.mark_execution_failed_by_watchdog.assert_not_called()
        mock_slot.release_slot.assert_not_called()

    @patch("services.cleanup_service.httpx.AsyncClient")
    @patch("services.cleanup_service.db")
    @patch("services.cleanup_service.get_slot_service")
    @patch("services.cleanup_service.get_execution_queue")
    def test_race_condition_db_update_noop(self, mock_queue_fn, mock_slot_fn, mock_db, mock_httpx):
        """When DB update returns False (race), skip slot/queue release."""
        mock_cm, _ = self._mock_httpx_client()
        mock_httpx.return_value = mock_cm

        mock_db.get_running_executions_with_agent_info.return_value = [
            {"id": "exec-1", "agent_name": "agent-a", "started_at": _past_iso(10), "timeout_seconds": 900, "schedule_id": "s1"},
        ]
        mock_db.mark_execution_failed_by_watchdog.return_value = False

        mock_slot = AsyncMock()
        mock_slot_fn.return_value = mock_slot
        mock_q = AsyncMock()
        mock_queue_fn.return_value = mock_q

        service = self._make_service()
        service._get_agent_running_ids = AsyncMock(return_value=set())
        service._broadcast_watchdog_event = AsyncMock()

        orphaned, terminated, confirmed_running = asyncio.run(
            service._reconcile_orphaned_executions()
        )

        assert orphaned == 0
        assert confirmed_running == set()
        mock_slot.release_slot.assert_not_called()
        mock_q.force_release_if_matches.assert_not_called()

    @patch("services.cleanup_service.httpx.AsyncClient")
    @patch("services.cleanup_service.db")
    @patch("services.cleanup_service.get_slot_service")
    @patch("services.cleanup_service.get_execution_queue")
    def test_per_execution_error_isolation(self, mock_queue_fn, mock_slot_fn, mock_db, mock_httpx):
        """One execution's failure doesn't block recovery of others."""
        mock_cm, _ = self._mock_httpx_client()
        mock_httpx.return_value = mock_cm

        mock_db.get_running_executions_with_agent_info.return_value = [
            {"id": "exec-BAD", "agent_name": "agent-a", "started_at": _past_iso(10), "timeout_seconds": 900, "schedule_id": "s1"},
            {"id": "exec-GOOD", "agent_name": "agent-a", "started_at": _past_iso(10), "timeout_seconds": 900, "schedule_id": "s1"},
        ]

        mock_db.mark_execution_failed_by_watchdog.side_effect = [
            Exception("DB error on first"),
            True,
        ]

        mock_slot = AsyncMock()
        mock_slot_fn.return_value = mock_slot
        mock_q = AsyncMock()
        mock_queue_fn.return_value = mock_q

        service = self._make_service()
        service._get_agent_running_ids = AsyncMock(return_value=set())
        service._broadcast_watchdog_event = AsyncMock()

        orphaned, terminated, confirmed_running = asyncio.run(
            service._reconcile_orphaned_executions()
        )

        assert orphaned == 1
        assert confirmed_running == set()
        assert mock_db.mark_execution_failed_by_watchdog.call_count == 2


# ---------------------------------------------------------------------------
# WebSocket broadcast tests
# ---------------------------------------------------------------------------

class TestBroadcastWatchdogEvent:
    """Tests for _broadcast_watchdog_event()."""

    pytestmark = pytest.mark.unit

    def test_noop_when_ws_manager_none(self):
        """No error when WebSocket manager is not set."""
        from services.cleanup_service import CleanupService
        import services.cleanup_service as cs_module

        original = cs_module._ws_manager
        cs_module._ws_manager = None
        try:
            service = CleanupService()
            # Should not raise
            asyncio.run(
                service._broadcast_watchdog_event("orphan_recovered", "agent-a", "exec-1", "test reason")
            )
        finally:
            cs_module._ws_manager = original

    def test_broadcasts_correct_event_format(self):
        """WebSocket event has correct JSON structure."""
        from services.cleanup_service import CleanupService
        import services.cleanup_service as cs_module

        mock_manager = MagicMock()
        mock_manager.broadcast = AsyncMock()

        original = cs_module._ws_manager
        cs_module._ws_manager = mock_manager
        try:
            service = CleanupService()
            asyncio.run(
                service._broadcast_watchdog_event("auto_terminated", "agent-x", "exec-42", "timed out")
            )

            mock_manager.broadcast.assert_called_once()
            event_json = mock_manager.broadcast.call_args[0][0]
            event = json.loads(event_json)

            assert event["type"] == "watchdog_recovery"
            assert event["agent_name"] == "agent-x"
            assert event["execution_id"] == "exec-42"
            assert event["action"] == "auto_terminated"
            assert event["reason"] == "timed out"
            assert "timestamp" in event
        finally:
            cs_module._ws_manager = original
