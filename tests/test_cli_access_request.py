"""
CLI Access Request Tests (test_cli_access_request.py)

Tests for the POST /api/access/request endpoint (CLI-002).
Auto-approve whitelist registration for CLI onboarding.

FAST TESTS - No agent creation required.
"""

import uuid
import pytest

pytestmark = pytest.mark.smoke

from utils.api_client import TrinityApiClient
from utils.assertions import (
    assert_status,
    assert_json_response,
    assert_has_fields,
)


def _unique_email():
    """Generate a unique test email to avoid whitelist collisions."""
    return f"cli-test-{uuid.uuid4().hex[:8]}@example.com"


class TestAccessRequestEndpoint:
    """Tests for POST /api/access/request."""

    def test_access_request_grants_new_email(self, unauthenticated_client: TrinityApiClient):
        """New email is auto-whitelisted and returns already_registered=False."""
        email = _unique_email()
        response = unauthenticated_client.post(
            "/api/access/request",
            json={"email": email},
            auth=False,
        )

        assert_status(response, 200)
        data = assert_json_response(response)
        assert_has_fields(data, ["success", "message", "already_registered"])
        assert data["success"] is True
        assert data["already_registered"] is False

    def test_access_request_idempotent(self, unauthenticated_client: TrinityApiClient):
        """Calling twice for same email returns already_registered=True."""
        email = _unique_email()

        # First call
        unauthenticated_client.post(
            "/api/access/request",
            json={"email": email},
            auth=False,
        )

        # Second call
        response = unauthenticated_client.post(
            "/api/access/request",
            json={"email": email},
            auth=False,
        )

        assert_status(response, 200)
        data = response.json()
        assert data["success"] is True
        assert data["already_registered"] is True

    def test_access_request_missing_email(self, unauthenticated_client: TrinityApiClient):
        """Missing email returns 400."""
        response = unauthenticated_client.post(
            "/api/access/request",
            json={},
            auth=False,
        )

        assert_status(response, 400)

    def test_access_request_empty_email(self, unauthenticated_client: TrinityApiClient):
        """Empty email returns 400."""
        response = unauthenticated_client.post(
            "/api/access/request",
            json={"email": ""},
            auth=False,
        )

        assert_status(response, 400)

    def test_access_request_invalid_email(self, unauthenticated_client: TrinityApiClient):
        """Email without @ returns 400."""
        response = unauthenticated_client.post(
            "/api/access/request",
            json={"email": "not-an-email"},
            auth=False,
        )

        assert_status(response, 400)

    def test_access_request_normalizes_email(self, unauthenticated_client: TrinityApiClient):
        """Email is lowercased — uppercase variant returns already_registered."""
        base = uuid.uuid4().hex[:8]
        email_lower = f"cli-test-{base}@example.com"
        email_upper = f"CLI-TEST-{base}@EXAMPLE.COM"

        # Register lowercase
        unauthenticated_client.post(
            "/api/access/request",
            json={"email": email_lower},
            auth=False,
        )

        # Check uppercase is recognized as same
        response = unauthenticated_client.post(
            "/api/access/request",
            json={"email": email_upper},
            auth=False,
        )

        assert_status(response, 200)
        data = response.json()
        assert data["already_registered"] is True

    def test_access_request_response_fields(self, unauthenticated_client: TrinityApiClient):
        """Response has exactly the expected fields."""
        email = _unique_email()
        response = unauthenticated_client.post(
            "/api/access/request",
            json={"email": email},
            auth=False,
        )

        assert_status(response, 200)
        data = response.json()
        assert set(data.keys()) == {"success", "message", "already_registered"}

    def test_access_request_whitelists_for_login(
        self, unauthenticated_client: TrinityApiClient
    ):
        """After access request, email can request a login code (proves whitelist works)."""
        email = _unique_email()

        # Register
        reg_response = unauthenticated_client.post(
            "/api/access/request",
            json={"email": email},
            auth=False,
        )
        assert_status(reg_response, 200)

        # Now request login code — should succeed (not silently fail)
        login_response = unauthenticated_client.post(
            "/api/auth/email/request",
            json={"email": email},
            auth=False,
        )

        assert_status(login_response, 200)
        data = login_response.json()
        assert data["success"] is True


class TestAccessRequestCleanup:
    """Cleanup test whitelist entries created during testing."""

    def test_cleanup_test_emails(self, api_client: TrinityApiClient):
        """Remove any cli-test-* emails from whitelist (best-effort cleanup)."""
        # List whitelist
        response = api_client.get("/api/settings/email-whitelist")
        if response.status_code != 200:
            pytest.skip("Cannot access whitelist endpoint")

        data = response.json()
        emails = data if isinstance(data, list) else data.get("emails", [])

        for entry in emails:
            email = entry.get("email", entry) if isinstance(entry, dict) else entry
            if isinstance(email, str) and email.startswith("cli-test-"):
                api_client.delete(f"/api/settings/email-whitelist/{email}")
