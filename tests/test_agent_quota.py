"""
Agent Quota Enforcement Tests (test_agent_quota.py)

Tests for per-user agent creation limits (QUOTA-001).
Covers: max_agents_per_user setting, HTTP 429 on exceed,
quota enforcement in both create and deploy-local paths,
system agent exclusion, and redeploy bypass.

Feature Flow: cli-tool.md (Agent Quota Enforcement section)
"""

import pytest
import uuid
import base64
import tarfile
import io
import time

from utils.api_client import TrinityApiClient
from utils.assertions import (
    assert_status,
    assert_status_in,
    assert_json_response,
)
from utils.cleanup import cleanup_test_agent


def create_test_archive(name: str) -> str:
    """Create a minimal valid deploy archive for quota tests."""
    template_content = f"""
name: {name}
display_name: Quota Test Agent
resources:
  cpu: "1"
  memory: "2g"
"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        data = template_content.encode('utf-8')
        tarinfo = tarfile.TarInfo(name='template.yaml')
        tarinfo.size = len(data)
        tar.addfile(tarinfo, io.BytesIO(data))

        claude_md = b"# Test Agent\nQuota test."
        tarinfo2 = tarfile.TarInfo(name='CLAUDE.md')
        tarinfo2.size = len(claude_md)
        tar.addfile(tarinfo2, io.BytesIO(claude_md))

    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('utf-8')


class TestAgentQuotaSetting:
    """Tests for max_agents_per_user setting."""

    pytestmark = pytest.mark.smoke

    def test_default_quota_is_three(self, api_client: TrinityApiClient):
        """Default max_agents_per_user should be 3 (or not set, implying default)."""
        response = api_client.get("/api/settings/max_agents_per_user")
        # Either 404 (not set, default applies) or 200 with value "3"
        if response.status_code == 200:
            data = response.json()
            assert data["value"] == "3"
        else:
            # Setting not explicitly set — code defaults to "3"
            assert response.status_code == 404

    def test_quota_setting_can_be_updated(self, api_client: TrinityApiClient):
        """Admin can change the agent quota limit."""
        try:
            response = api_client.put(
                "/api/settings/max_agents_per_user",
                json={"value": "5"}
            )
            assert_status(response, 200)
            data = response.json()
            assert data["value"] == "5"

            # Verify persisted
            response = api_client.get("/api/settings/max_agents_per_user")
            assert_status(response, 200)
            assert response.json()["value"] == "5"
        finally:
            # Restore default
            api_client.delete("/api/settings/max_agents_per_user")

    def test_quota_zero_disables_limit(self, api_client: TrinityApiClient):
        """Setting quota to 0 disables the agent limit."""
        try:
            response = api_client.put(
                "/api/settings/max_agents_per_user",
                json={"value": "0"}
            )
            assert_status(response, 200)
            assert response.json()["value"] == "0"
        finally:
            api_client.delete("/api/settings/max_agents_per_user")


def _count_non_system_agents(api_client: TrinityApiClient) -> int:
    """Count current non-system agents (matching quota logic which excludes system agents)."""
    resp = api_client.get("/api/agents")
    if resp.status_code == 200:
        agents = resp.json()
        # Exclude system agents — quota logic does the same
        return len([a for a in agents if a.get("name") != "trinity-system"])
    return 0


class TestAgentQuotaEnforcement:
    """Tests for quota enforcement on agent creation."""

    pytestmark = pytest.mark.smoke

    def test_create_agent_returns_429_at_quota(self, api_client: TrinityApiClient):
        """Creating an agent beyond the quota returns HTTP 429."""
        agents_created = []
        try:
            # Set quota to current count + 1 so we can create exactly one more
            existing = _count_non_system_agents(api_client)
            quota = existing + 1
            api_client.put(
                "/api/settings/max_agents_per_user",
                json={"value": str(quota)}
            )

            # Create first agent (should succeed — fills the quota)
            name1 = f"quota-test-{uuid.uuid4().hex[:6]}"
            resp1 = api_client.post("/api/agents", json={"name": name1})
            assert_status_in(resp1, [200, 201])
            agents_created.append(name1)

            # Wait for agent to register
            time.sleep(2)

            # Create second agent (should fail with 429)
            name2 = f"quota-test-{uuid.uuid4().hex[:6]}"
            resp2 = api_client.post("/api/agents", json={"name": name2})
            assert_status(resp2, 429)
            data = resp2.json()
            detail = data.get("detail", {})
            if isinstance(detail, dict):
                assert detail.get("code") == "QUOTA_EXCEEDED"
            else:
                assert "quota" in str(detail).lower()

        finally:
            api_client.delete("/api/settings/max_agents_per_user")
            for name in agents_created:
                cleanup_test_agent(api_client, name)

    def test_deploy_local_returns_429_at_quota(self, api_client: TrinityApiClient):
        """Deploying a new agent via deploy-local beyond quota returns HTTP 429."""
        agents_created = []
        try:
            existing = _count_non_system_agents(api_client)
            quota = existing + 1
            api_client.put(
                "/api/settings/max_agents_per_user",
                json={"value": str(quota)}
            )

            # Deploy first agent (fills quota)
            name1 = f"quota-deploy-{uuid.uuid4().hex[:6]}"
            archive1 = create_test_archive(name1)
            resp1 = api_client.post(
                "/api/agents/deploy-local",
                json={"archive": archive1, "name": name1}
            )
            assert_status(resp1, 200)
            agents_created.append(name1)

            time.sleep(2)

            # Deploy second agent (should fail with 429)
            name2 = f"quota-deploy-{uuid.uuid4().hex[:6]}"
            archive2 = create_test_archive(name2)
            resp2 = api_client.post(
                "/api/agents/deploy-local",
                json={"archive": archive2, "name": name2}
            )
            assert_status(resp2, 429)
            data = resp2.json()
            detail = data.get("detail", {})
            if isinstance(detail, dict):
                assert detail.get("code") == "QUOTA_EXCEEDED"
            else:
                assert "quota" in str(detail).lower()

        finally:
            api_client.delete("/api/settings/max_agents_per_user")
            for name in agents_created:
                cleanup_test_agent(api_client, name)

    def test_quota_exceeded_message_includes_limit(self, api_client: TrinityApiClient):
        """The 429 response includes the quota limit in the message."""
        agents_created = []
        try:
            existing = _count_non_system_agents(api_client)
            quota = existing + 1
            api_client.put(
                "/api/settings/max_agents_per_user",
                json={"value": str(quota)}
            )

            name1 = f"quota-msg-{uuid.uuid4().hex[:6]}"
            resp1 = api_client.post("/api/agents", json={"name": name1})
            assert_status_in(resp1, [200, 201])
            agents_created.append(name1)

            time.sleep(2)

            name2 = f"quota-msg-{uuid.uuid4().hex[:6]}"
            resp2 = api_client.post("/api/agents", json={"name": name2})
            assert_status(resp2, 429)
            detail = resp2.json().get("detail", {})
            error_msg = detail.get("error", "") if isinstance(detail, dict) else str(detail)
            assert str(quota) in error_msg  # Quota limit appears in message

        finally:
            api_client.delete("/api/settings/max_agents_per_user")
            for name in agents_created:
                cleanup_test_agent(api_client, name)


class TestAgentQuotaDisabled:
    """Tests for when quota is disabled (set to 0)."""

    pytestmark = pytest.mark.smoke

    def test_zero_quota_allows_unlimited_creation(self, api_client: TrinityApiClient):
        """When max_agents_per_user=0, no limit is enforced."""
        agents_created = []
        try:
            api_client.put(
                "/api/settings/max_agents_per_user",
                json={"value": "0"}
            )

            # Create 2 agents (would fail if quota=1 was enforced)
            for i in range(2):
                name = f"quota-off-{uuid.uuid4().hex[:6]}"
                resp = api_client.post("/api/agents", json={"name": name})
                assert_status_in(resp, [200, 201])
                agents_created.append(name)
                time.sleep(1)

        finally:
            api_client.delete("/api/settings/max_agents_per_user")
            for name in agents_created:
                cleanup_test_agent(api_client, name)


class TestAgentQuotaRedeploy:
    """Tests for quota bypass on redeploys."""

    @pytest.mark.slow
    @pytest.mark.requires_agent
    @pytest.mark.timeout(180)
    def test_redeploy_existing_agent_bypasses_quota(self, api_client: TrinityApiClient):
        """Redeploying an existing agent should not count against quota."""
        agents_created = []
        try:
            api_client.put(
                "/api/settings/max_agents_per_user",
                json={"value": "1"}
            )

            # Deploy first agent
            name = f"quota-redeploy-{uuid.uuid4().hex[:6]}"
            archive = create_test_archive(name)
            resp1 = api_client.post(
                "/api/agents/deploy-local",
                json={"archive": archive, "name": name}
            )
            assert_status(resp1, 200)
            agents_created.append(name)

            time.sleep(5)

            # Redeploy same base name — should succeed (creates versioned name)
            archive2 = create_test_archive(name)
            resp2 = api_client.post(
                "/api/agents/deploy-local",
                json={"archive": archive2, "name": name}
            )
            assert_status(resp2, 200)
            data = resp2.json()
            assert data.get("status") == "success"

            # Track the versioned name for cleanup
            versioned_name = data.get("agent", {}).get("name")
            if versioned_name and versioned_name != name:
                agents_created.append(versioned_name)

        finally:
            api_client.delete("/api/settings/max_agents_per_user")
            for n in agents_created:
                cleanup_test_agent(api_client, n)
