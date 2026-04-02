"""
CLI Profile Management Tests (test_cli_profiles.py)

Unit tests for multi-instance profile support in the Trinity CLI.
Tests config migration, profile CRUD, resolution priority, and CLI commands.

FAST TESTS - No backend required (pure config/file manipulation).
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from trinity_cli.config import (
    _is_legacy_config,
    _migrate_legacy_config,
    load_config,
    save_config,
    get_instance_url,
    get_api_key,
    get_user,
    set_auth,
    clear_auth,
    list_profiles,
    set_current_profile,
    remove_profile,
    profile_name_from_url,
    _resolve_profile_name,
    CONFIG_FILE,
)
from trinity_cli.main import cli


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    config_dir = tmp_path / ".trinity"
    config_dir.mkdir()
    config_file = config_dir / "config.json"
    monkeypatch.setattr("trinity_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("trinity_cli.config.CONFIG_FILE", config_file)
    # Clear env vars that would override
    monkeypatch.delenv("TRINITY_URL", raising=False)
    monkeypatch.delenv("TRINITY_API_KEY", raising=False)
    monkeypatch.delenv("TRINITY_PROFILE", raising=False)
    return config_file


class TestLegacyMigration:
    """Auto-migration of flat config to profile format."""

    def test_detects_legacy_config(self):
        assert _is_legacy_config({"instance_url": "http://localhost:8000", "token": "abc"})
        assert _is_legacy_config({"token": "abc"})
        assert _is_legacy_config({"instance_url": "http://localhost:8000"})

    def test_does_not_detect_new_config(self):
        assert not _is_legacy_config({"current_profile": "default", "profiles": {}})
        assert not _is_legacy_config({})

    def test_migrates_flat_to_default_profile(self):
        old = {
            "instance_url": "http://localhost:8000",
            "token": "mytoken",
            "user": {"email": "test@example.com"},
        }
        new = _migrate_legacy_config(old)
        assert new["current_profile"] == "default"
        assert new["profiles"]["default"]["instance_url"] == "http://localhost:8000"
        assert new["profiles"]["default"]["token"] == "mytoken"
        assert new["profiles"]["default"]["user"]["email"] == "test@example.com"

    def test_load_config_auto_migrates(self, tmp_config):
        # Write legacy format
        tmp_config.write_text(json.dumps({
            "instance_url": "http://old.example.com",
            "token": "legacytoken",
        }))
        config = load_config()
        assert "profiles" in config
        assert config["profiles"]["default"]["token"] == "legacytoken"
        # Verify it was persisted
        raw = json.loads(tmp_config.read_text())
        assert "profiles" in raw

    def test_empty_config_returns_structure(self, tmp_config):
        config = load_config()
        assert config == {"current_profile": "default", "profiles": {}}


class TestProfileCRUD:
    """Create, read, update, delete profiles."""

    def test_set_auth_creates_profile(self, tmp_config):
        set_auth("http://a.example.com", "token-a", {"email": "a@example.com"}, profile_name="site-a")
        config = load_config()
        assert "site-a" in config["profiles"]
        assert config["profiles"]["site-a"]["instance_url"] == "http://a.example.com"
        assert config["current_profile"] == "site-a"

    def test_set_auth_multiple_profiles(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        config = load_config()
        assert len(config["profiles"]) == 2
        assert config["current_profile"] == "b"  # last set becomes active

    def test_list_profiles(self, tmp_config):
        set_auth("http://a.example.com", "token-a", {"email": "a@example.com"}, profile_name="a")
        set_auth("http://b.example.com", "token-b", {"email": "b@example.com"}, profile_name="b")
        profiles = list_profiles()
        assert len(profiles) == 2
        names = {p["name"] for p in profiles}
        assert names == {"a", "b"}
        # "b" is active (last set)
        active = [p for p in profiles if p["active"]]
        assert len(active) == 1
        assert active[0]["name"] == "b"

    def test_switch_profile(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        assert set_current_profile("a") is True
        config = load_config()
        assert config["current_profile"] == "a"

    def test_switch_nonexistent_profile(self, tmp_config):
        assert set_current_profile("nope") is False

    def test_remove_profile(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        assert remove_profile("a") is True
        config = load_config()
        assert "a" not in config["profiles"]
        assert "b" in config["profiles"]

    def test_remove_active_profile_switches(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        set_current_profile("b")
        remove_profile("b")
        config = load_config()
        assert config["current_profile"] == "a"

    def test_remove_nonexistent_profile(self, tmp_config):
        assert remove_profile("nope") is False

    def test_clear_auth_clears_active_profile(self, tmp_config):
        set_auth("http://a.example.com", "token-a", {"email": "a@example.com"}, profile_name="a")
        clear_auth("a")
        profile = load_config()["profiles"]["a"]
        assert "token" not in profile
        assert "user" not in profile
        assert profile["instance_url"] == "http://a.example.com"


class TestProfileResolution:
    """Priority: env vars > explicit profile > TRINITY_PROFILE > current_profile."""

    def test_env_var_overrides_profile_url(self, tmp_config, monkeypatch):
        set_auth("http://config.example.com", "config-token", profile_name="p")
        monkeypatch.setenv("TRINITY_URL", "http://env.example.com")
        assert get_instance_url("p") == "http://env.example.com"

    def test_env_var_overrides_profile_key(self, tmp_config, monkeypatch):
        set_auth("http://config.example.com", "config-token", profile_name="p")
        monkeypatch.setenv("TRINITY_API_KEY", "env-key")
        assert get_api_key("p") == "env-key"

    def test_explicit_profile_name(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        set_current_profile("b")
        # Explicit name overrides current
        assert get_instance_url("a") == "http://a.example.com"
        assert get_api_key("a") == "token-a"

    def test_trinity_profile_env_var(self, tmp_config, monkeypatch):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        set_current_profile("b")
        monkeypatch.setenv("TRINITY_PROFILE", "a")
        assert _resolve_profile_name() == "a"
        assert get_instance_url() == "http://a.example.com"

    def test_falls_back_to_current_profile(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_current_profile("a")
        assert get_instance_url() == "http://a.example.com"

    def test_get_user_from_profile(self, tmp_config):
        set_auth("http://a.example.com", "token-a", {"email": "a@example.com"}, profile_name="a")
        assert get_user("a")["email"] == "a@example.com"

    def test_no_profile_returns_none(self, tmp_config):
        assert get_instance_url() is None
        assert get_api_key() is None
        assert get_user() is None


class TestProfileNameFromURL:
    """Derive profile names from instance URLs."""

    def test_localhost(self):
        assert profile_name_from_url("http://localhost:8000") == "localhost"

    def test_domain(self):
        assert profile_name_from_url("https://trinity.example.com") == "trinity.example.com"

    def test_subdomain(self):
        assert profile_name_from_url("https://staging.trinity.example.com/api") == "staging.trinity.example.com"

    def test_ip(self):
        assert profile_name_from_url("http://192.168.1.100:8000") == "192.168.1.100"


class TestCLIProfileCommands:
    """Test the `trinity profile` CLI commands via CliRunner."""

    def test_profile_list_empty(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0
        assert "No profiles configured" in result.output

    def test_profile_list_shows_profiles(self, tmp_config):
        set_auth("http://a.example.com", "token-a", {"email": "a@example.com"}, profile_name="dev")
        set_auth("http://b.example.com", "token-b", {"email": "b@example.com"}, profile_name="prod")
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0
        assert "dev" in result.output
        assert "prod" in result.output
        assert "*" in result.output  # active marker

    def test_profile_list_json(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="dev")
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "list", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["name"] == "dev"

    def test_profile_use(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "use", "a"])
        assert result.exit_code == 0
        assert "Switched to profile 'a'" in result.output
        assert load_config()["current_profile"] == "a"

    def test_profile_use_nonexistent(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "use", "nope"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_profile_remove(self, tmp_config):
        set_auth("http://a.example.com", "token-a", profile_name="a")
        set_auth("http://b.example.com", "token-b", profile_name="b")
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "remove", "a"])
        assert result.exit_code == 0
        assert "Removed profile 'a'" in result.output
        assert "a" not in load_config()["profiles"]

    def test_profile_remove_nonexistent(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "remove", "nope"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_global_profile_flag(self, tmp_config):
        """--profile flag on root command is passed through context."""
        set_auth("http://a.example.com", "token-a", {"email": "a@example.com"}, profile_name="a")
        set_auth("http://b.example.com", "token-b", {"email": "b@example.com"}, profile_name="b")
        set_current_profile("b")
        runner = CliRunner()
        result = runner.invoke(cli, ["--profile", "a", "status"])
        assert result.exit_code == 0
        assert "a.example.com" in result.output
        assert "Profile:  a" in result.output
