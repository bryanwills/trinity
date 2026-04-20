"""
Unit test for #418 — FanOutService must not apply the batch deadline as the
per-subtask timeout.

Pre-fix: `execute()` passed its `timeout_seconds` (overall deadline) to each
`execute_task` call, overriding the target agent's configured
`execution_timeout_seconds`. A 600s batch deadline killed 1800s sub-tasks at
610s.

Post-fix: per-subtask timeout is left as `None` so TaskExecutionService falls
back to the target agent's config (TIMEOUT-001 semantics). The batch deadline
remains as the overall fan-out wall-clock cap.
"""

import asyncio
import importlib.util
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

_candidates = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "backend")),
    "/app",
]
_backend = next(
    (p for p in _candidates if os.path.isfile(os.path.join(p, "services", "fan_out_service.py"))),
    None,
)
assert _backend, "Could not locate backend path containing services/fan_out_service.py"
if _backend not in sys.path:
    sys.path.insert(0, _backend)


@pytest.fixture
def fan_out_service(monkeypatch):
    """Load FanOutService with task_execution_service stubbed."""
    # Stub task_execution_service so import resolution short-circuits.
    fake_task_exec_mod = types.ModuleType("services.task_execution_service")

    class _Result:
        def __init__(self, status="success", response="ok"):
            self.status = status
            self.response = response
            self.error = None
            self.error_code = None
            self.cost = 0.0
            self.context_used = 0
            self.execution_id = "exec-1"

    fake_task_service = MagicMock()
    fake_task_service.execute_task = AsyncMock(return_value=_Result())

    fake_task_exec_mod.TaskExecutionResult = _Result
    fake_task_exec_mod.TaskExecutionErrorCode = MagicMock()
    fake_task_exec_mod.get_task_execution_service = lambda: fake_task_service
    monkeypatch.setitem(sys.modules, "services.task_execution_service", fake_task_exec_mod)

    # Direct-load fan_out_service to avoid pulling services/__init__.py.
    spec = importlib.util.spec_from_file_location(
        "services.fan_out_service",
        os.path.join(_backend, "services", "fan_out_service.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FanOutService(), mod.FanOutTaskInput, fake_task_service


def test_per_subtask_timeout_is_none_not_batch_deadline(fan_out_service):
    """
    execute_task must receive timeout_seconds=None so the backend resolves it
    to the target agent's execution_timeout_seconds (TIMEOUT-001), rather than
    the fan-out batch deadline.
    """
    service, FanOutTaskInput, task_svc = fan_out_service
    tasks = [
        FanOutTaskInput(id="t1", message="m1"),
        FanOutTaskInput(id="t2", message="m2"),
    ]

    asyncio.run(
        service.execute(
            agent_name="delegate",
            tasks=tasks,
            max_concurrency=2,
            timeout_seconds=600,  # batch deadline — must NOT leak to subtasks
        )
    )

    assert task_svc.execute_task.await_count == 2
    for call in task_svc.execute_task.await_args_list:
        assert call.kwargs["timeout_seconds"] is None, (
            f"execute_task should get None per-subtask timeout, got "
            f"{call.kwargs['timeout_seconds']} — this would reintroduce #418"
        )
        # Sanity: other fields still flow through.
        assert call.kwargs["triggered_by"] == "fan_out"
        assert call.kwargs["agent_name"] == "delegate"


def test_subtasks_complete_within_batch_deadline(fan_out_service):
    """Smoke test the happy path still returns a FanOutResult."""
    service, FanOutTaskInput, _ = fan_out_service
    tasks = [FanOutTaskInput(id="only", message="hi")]

    result = asyncio.run(
        service.execute(
            agent_name="delegate",
            tasks=tasks,
            max_concurrency=1,
            timeout_seconds=300,
        )
    )

    assert result.completed == 1
    assert result.failed == 0
    # `results` is an ordered list, not a dict — find by id.
    by_id = {r.id: r for r in result.results}
    assert by_id["only"].status == "completed"
