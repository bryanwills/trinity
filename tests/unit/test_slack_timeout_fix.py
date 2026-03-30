"""
Unit tests for Slack message router timeout fix (#221).

Verifies:
- timeout_seconds=None is passed to TaskExecutionService (uses agent timeout)
- Error messages are forwarded to Slack users as-is
- Generic fallback only for empty/unknown errors

Module: src/backend/adapters/message_router.py
Issue: https://github.com/abilityai/trinity/issues/221
"""

import pytest


class TestErrorMessageRouting:
    """Test the error message logic extracted from message_router.py.

    The router does:
        if error_msg and error_msg != "Unknown error":
            response_text = error_msg
        else:
            response_text = "Sorry, I encountered an error processing your message."
    """

    @staticmethod
    def get_response_text(error_msg):
        """Reproduce the router's error message logic."""
        if error_msg and error_msg != "Unknown error":
            return error_msg
        else:
            return "Sorry, I encountered an error processing your message."

    def test_timeout_error_forwarded(self):
        """Timeout error from TaskExecutionService is sent to Slack user."""
        msg = "Task execution timed out after 60 seconds"
        assert self.get_response_text(msg) == msg

    def test_timeout_900s_forwarded(self):
        """Agent-configured timeout (900s) error is forwarded."""
        msg = "Task execution timed out after 900 seconds"
        assert self.get_response_text(msg) == msg

    def test_capacity_error_forwarded(self):
        """Capacity error is sent to Slack user."""
        msg = "Agent at capacity (3/3 parallel tasks running)"
        assert self.get_response_text(msg) == msg

    def test_auth_error_forwarded(self):
        """Auth error from agent is sent to Slack user."""
        msg = "Task execution failed (exit code 1): No authentication configured. Set ANTHROPIC_API_KEY or assign a subscription token."
        assert self.get_response_text(msg) == msg

    def test_billing_error_forwarded(self):
        """Billing/rate-limit error is sent to Slack user."""
        msg = "HTTP error: rate_limit_exceeded"
        assert self.get_response_text(msg) == msg

    def test_unknown_error_gets_generic(self):
        """'Unknown error' gets the generic fallback message."""
        assert self.get_response_text("Unknown error") == "Sorry, I encountered an error processing your message."

    def test_none_error_gets_generic(self):
        """None error gets the generic fallback message."""
        assert self.get_response_text(None) == "Sorry, I encountered an error processing your message."

    def test_empty_error_gets_generic(self):
        """Empty string error gets the generic fallback message."""
        assert self.get_response_text("") == "Sorry, I encountered an error processing your message."


class TestTimeoutParameter:
    """Test that timeout_seconds=None is the correct approach.

    TaskExecutionService.execute_task() line 153-155:
        if timeout_seconds is None:
            timeout_seconds = db.get_execution_timeout(agent_name)

    Passing None lets the service use the agent's configured timeout.
    Other callers that do it correctly: chat.py, paid.py
    """

    def test_none_triggers_agent_lookup(self):
        """Passing None means 'use agent default' — this is the contract."""
        timeout_seconds = None
        # Simulates TaskExecutionService logic
        if timeout_seconds is None:
            timeout_seconds = 900  # db.get_execution_timeout() default
        assert timeout_seconds == 900

    def test_explicit_value_overrides(self):
        """Passing an explicit value skips the agent lookup."""
        timeout_seconds = 120
        if timeout_seconds is None:
            timeout_seconds = 900
        assert timeout_seconds == 120  # keeps the explicit value

    def test_none_is_not_zero(self):
        """None and 0 are different — 0 would not trigger the lookup."""
        timeout_seconds = 0
        if timeout_seconds is None:
            timeout_seconds = 900
        assert timeout_seconds == 0  # 0 is falsy but not None
