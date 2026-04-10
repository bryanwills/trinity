"""
Tests for Claude Code startup watchdog (#285).

The watchdog kills the `claude` subprocess if it produces no output within
STARTUP_TIMEOUT_SECONDS, converting an hour-long zombie execution (caused
by expired OAuth tokens hanging Claude Code) into a fast-fail with an
actionable error message.

Two classes of test:

1. Pure-mock tests that stub `subprocess.Popen` and exercise the watchdog
   helper + the reader loop in isolation. Fast, deterministic, cover
   control flow.

2. Real-subprocess tests that spawn actual processes (`sh -c 'sleep ...'`)
   to catch pipe-buffer deadlocks and Popen-lifecycle bugs that mocks
   cannot reproduce. The big risk we're defending against is a stderr
   buffer filling up and blocking the child process while the watchdog
   waits for stdout.

Module: docker/base-image/agent_server/services/claude_code.py
"""
import io
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# The agent-server module lives in docker/base-image and has heavy imports
# (fastapi, MCP, credential_sanitizer, etc.) — we don't need any of that
# to test the watchdog helper in isolation. Load only the specific helpers.
_project_root = Path(__file__).resolve().parents[2]
_watchdog_path = _project_root / "docker" / "base-image" / "agent_server" / "services" / "claude_code.py"


def _load_watchdog_symbols():
    """
    Load _spawn_startup_watchdog and related constants from claude_code.py
    without triggering its heavy imports.

    We extract the helper by parsing the module source and exec-ing only
    the top-level helper definitions into a fresh namespace.
    """
    source = _watchdog_path.read_text()
    import ast
    tree = ast.parse(source)

    wanted_names = {
        "_spawn_startup_watchdog",
        "STARTUP_TIMEOUT_ERROR_MESSAGE",
        "STARTUP_TIMEOUT_SECONDS",
        "logger",  # module-level logger used by the watchdog helper
    }

    # Keep only top-level nodes we need: imports (minus relative ones) plus
    # the specific definitions.
    keep = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            keep.append(node)
        elif isinstance(node, ast.ImportFrom):
            # Skip relative imports (..models, ..state) — they need the
            # real package hierarchy we don't want to load.
            if node.level == 0 and node.module not in {"fastapi"}:
                # Also skip fastapi to avoid requiring it in the test venv.
                keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_names:
            keep.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in wanted_names:
            keep.append(node)
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(name in wanted_names for name in targets):
                keep.append(node)

    new_tree = ast.Module(body=keep, type_ignores=[])
    code = compile(new_tree, str(_watchdog_path), "exec")
    ns: dict = {"__name__": "claude_code_watchdog_test"}
    exec(code, ns)
    return ns


_ws = _load_watchdog_symbols()
_spawn_startup_watchdog = _ws["_spawn_startup_watchdog"]
STARTUP_TIMEOUT_ERROR_MESSAGE = _ws["STARTUP_TIMEOUT_ERROR_MESSAGE"]
STARTUP_TIMEOUT_SECONDS = _ws["STARTUP_TIMEOUT_SECONDS"]


# ---------------------------------------------------------------------------
# Pure-mock tests — exercise the watchdog helper without real subprocesses
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Popen stand-in that records whether kill() was called."""

    def __init__(self, already_exited: bool = False):
        self._already_exited = already_exited
        self.killed = False

    def poll(self):
        return 0 if self._already_exited else None

    def kill(self):
        self.killed = True


class TestStartupWatchdogUnit:
    """Pure-mock tests for _spawn_startup_watchdog."""

    def test_no_kill_when_started_event_fires_first(self):
        """Happy path: process signals startup → watchdog is a no-op."""
        proc = _FakeProcess()
        started = threading.Event()
        fired = threading.Event()

        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=5,  # generous — we'll fire started almost immediately
            task_id="test-happy",
        )

        # Simulate the subprocess emitting output right away.
        time.sleep(0.05)
        started.set()

        thread.join(timeout=1.0)
        assert not thread.is_alive()
        assert proc.killed is False
        assert fired.is_set() is False

    def test_kill_when_started_event_never_fires(self):
        """Bug path: process produces no output → watchdog kills it."""
        proc = _FakeProcess()
        started = threading.Event()
        fired = threading.Event()

        start_time = time.monotonic()
        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=0.3,  # short for test speed
            task_id="test-hang",
        )

        thread.join(timeout=2.0)
        elapsed = time.monotonic() - start_time

        assert not thread.is_alive()
        assert proc.killed is True, "watchdog should have killed the process"
        assert fired.is_set() is True, "watchdog_fired flag should be set"
        # Watchdog should fire close to the threshold, not wildly later.
        assert elapsed < 1.0, f"watchdog took {elapsed:.2f}s to fire (threshold=0.3s)"

    def test_no_kill_when_process_already_exited(self):
        """Edge case: process died before threshold — nothing to kill."""
        proc = _FakeProcess(already_exited=True)
        started = threading.Event()
        fired = threading.Event()

        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=0.2,
            task_id="test-exited",
        )

        thread.join(timeout=1.0)
        assert not thread.is_alive()
        assert proc.killed is False, "should not kill an already-exited process"
        assert fired.is_set() is False, "should not mark watchdog_fired for natural exit"

    def test_thread_is_daemon(self):
        """Watchdog thread must be a daemon so it doesn't block interpreter exit."""
        proc = _FakeProcess()
        started = threading.Event()
        fired = threading.Event()

        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=10,
            task_id="test-daemon",
        )
        try:
            assert thread.daemon is True
        finally:
            started.set()  # clean up
            thread.join(timeout=1.0)

    def test_kill_exception_is_logged_not_raised(self):
        """If process.kill() raises, the watchdog thread must not propagate
        (it's a daemon; propagating just prints a noisy traceback and still
        leaves the main thread hung)."""
        proc = _FakeProcess()
        proc.kill = Mock(side_effect=OSError("process already dead"))
        started = threading.Event()
        fired = threading.Event()

        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=0.2,
            task_id="test-kill-fail",
        )

        thread.join(timeout=1.0)
        assert not thread.is_alive()
        # Fired flag is set BEFORE the kill attempt, so it reflects intent
        # regardless of whether kill() succeeded.
        assert fired.is_set() is True
        proc.kill.assert_called_once()


class TestConstants:
    """Verify module-level constants are sane."""

    def test_default_startup_timeout_is_reasonable(self):
        """Default must be long enough for MCP-heavy cold starts (>30s)
        but short enough to prevent the hour-long zombie (<300s)."""
        assert 30 <= STARTUP_TIMEOUT_SECONDS <= 300, (
            f"STARTUP_TIMEOUT_SECONDS={STARTUP_TIMEOUT_SECONDS} is outside "
            f"the sensible band [30, 300]"
        )

    def test_error_message_mentions_token_expiry(self):
        """Error must be actionable — mention what the user should do."""
        msg = STARTUP_TIMEOUT_ERROR_MESSAGE.format(timeout=60)
        assert "token" in msg.lower()
        assert "setup-token" in msg or "subscription" in msg.lower()

    def test_env_var_override(self):
        """Confirm the constant is loaded from CLAUDE_STARTUP_TIMEOUT env var."""
        # Re-read the source and check the assignment uses os.environ.
        source = _watchdog_path.read_text()
        assert 'os.environ.get("CLAUDE_STARTUP_TIMEOUT"' in source


# ---------------------------------------------------------------------------
# Real-subprocess tests — catch pipe-buffer deadlocks that mocks miss
# ---------------------------------------------------------------------------


class TestWatchdogWithRealSubprocess:
    """
    Tests that spawn actual subprocesses. Slower than mock tests (each runs
    a real /bin/sh) but catch the class of bugs where the watchdog works
    fine in isolation but deadlocks once you add real OS pipes into the mix.
    """

    def test_kills_real_silent_process(self):
        """A real subprocess that produces no output is killed by the watchdog."""
        # `sh -c 'sleep 10'` — no stdin consumption, no stdout, no stderr.
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 10"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        started = threading.Event()
        fired = threading.Event()

        start = time.monotonic()
        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=0.5,
            task_id="real-silent",
        )
        thread.join(timeout=3.0)
        elapsed = time.monotonic() - start

        try:
            assert fired.is_set()
            # Process should be dead (or dying) now.
            proc.wait(timeout=2.0)
            assert proc.returncode is not None
            # Real process kills should still happen near the threshold.
            assert elapsed < 2.0, f"took {elapsed:.2f}s to kill real process"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_no_kill_when_real_process_writes_output(self):
        """A real subprocess that emits stdout before the threshold is not killed."""
        # Emit a line immediately, then sleep.
        proc = subprocess.Popen(
            ["sh", "-c", "echo hello; sleep 5"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        started = threading.Event()
        fired = threading.Event()

        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=0.5,
            task_id="real-quickstart",
        )

        # Simulate the reader thread seeing the first line.
        # In production this happens inside read_subprocess_output; here we
        # just read one line and set the event.
        try:
            line = proc.stdout.readline()
            assert line.strip() == "hello"
            started.set()

            thread.join(timeout=1.5)
            assert not fired.is_set(), "watchdog should NOT fire after first output"
            assert proc.poll() is None, "process should still be running (sleep 5)"
        finally:
            proc.kill()
            proc.wait()

    def test_stderr_flood_does_not_deadlock_watchdog(self):
        """
        Regression test for the pipe-buffer deadlock class of bugs.

        A process that floods stderr while writing nothing to stdout can
        fill the stderr pipe buffer (~64KB on Linux) and then block on
        its next write. If the reader only drains stdout, the child
        deadlocks and the watchdog is the last line of defense.

        This test asserts the watchdog still fires within the threshold
        in that scenario. A naive implementation that polls process.stdout
        instead of using a timer-based event wait could deadlock here.
        """
        # Script: spam stderr hard, never write stdout, never exit.
        script = 'while true; do echo "spam spam spam" >&2; done'
        proc = subprocess.Popen(
            ["sh", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        started = threading.Event()
        fired = threading.Event()

        start = time.monotonic()
        thread = _spawn_startup_watchdog(
            process=proc,
            started_event=started,
            watchdog_fired=fired,
            timeout_seconds=0.5,
            task_id="real-stderr-flood",
        )
        thread.join(timeout=3.0)
        elapsed = time.monotonic() - start

        try:
            # The crucial assertion: watchdog fires even though the child is
            # filling stderr. Our watchdog uses started_event.wait() which
            # is independent of pipe I/O, so it's immune to this deadlock.
            assert fired.is_set(), "watchdog deadlocked when child flooded stderr"
            assert elapsed < 2.0, f"watchdog took {elapsed:.2f}s — possible deadlock"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
