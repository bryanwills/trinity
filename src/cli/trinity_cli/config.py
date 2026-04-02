"""Configuration management for Trinity CLI.

Supports named profiles for managing multiple Trinity instances.
Stores config in ~/.trinity/config.json with 0600 permissions.

Config format:
    {
        "current_profile": "local",
        "profiles": {
            "local": {
                "instance_url": "http://localhost:8000",
                "token": "eyJ...",
                "user": {"email": "admin@example.com"}
            }
        }
    }

Legacy flat configs are auto-migrated to a "default" profile on first access.
"""

import json
import os
import stat
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


CONFIG_DIR = Path.home() / ".trinity"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _ensure_config_dir():
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)


def _is_legacy_config(config: dict) -> bool:
    """Check if config uses the old flat format (no profiles key)."""
    return "profiles" not in config and ("instance_url" in config or "token" in config)


def _migrate_legacy_config(config: dict) -> dict:
    """Migrate flat config to profile-based format."""
    profile_data = {}
    for key in ("instance_url", "token", "user"):
        if key in config:
            profile_data[key] = config[key]

    if not profile_data:
        return {"current_profile": "default", "profiles": {}}

    return {
        "current_profile": "default",
        "profiles": {
            "default": profile_data,
        },
    }


def load_config() -> dict:
    """Load config, auto-migrating legacy flat format if needed."""
    if not CONFIG_FILE.exists():
        return {"current_profile": "default", "profiles": {}}
    config = json.loads(CONFIG_FILE.read_text())
    if _is_legacy_config(config):
        config = _migrate_legacy_config(config)
        save_config(config)
    return config


def save_config(config: dict):
    _ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def _resolve_profile_name(explicit_profile: Optional[str] = None) -> str:
    """Resolve which profile to use.

    Priority: explicit_profile arg > TRINITY_PROFILE env var > current_profile in config.
    """
    if explicit_profile:
        return explicit_profile
    env_profile = os.environ.get("TRINITY_PROFILE")
    if env_profile:
        return env_profile
    config = load_config()
    return config.get("current_profile", "default")


def get_profile(profile_name: Optional[str] = None) -> dict:
    """Get the data for a specific profile (or the active one)."""
    name = _resolve_profile_name(profile_name)
    config = load_config()
    return config.get("profiles", {}).get(name, {})


def get_instance_url(profile_name: Optional[str] = None) -> Optional[str]:
    """Get configured instance URL. Env var TRINITY_URL always wins."""
    url = os.environ.get("TRINITY_URL")
    if url:
        return url.rstrip("/")
    profile = get_profile(profile_name)
    url = profile.get("instance_url")
    return url.rstrip("/") if url else None


def get_api_key(profile_name: Optional[str] = None) -> Optional[str]:
    """Get API key/token. Env var TRINITY_API_KEY always wins."""
    key = os.environ.get("TRINITY_API_KEY")
    if key:
        return key
    profile = get_profile(profile_name)
    return profile.get("token")


def get_user(profile_name: Optional[str] = None) -> Optional[dict]:
    """Get user info from the active profile."""
    profile = get_profile(profile_name)
    return profile.get("user")


def set_auth(instance_url: str, token: str, user: Optional[dict] = None,
             profile_name: Optional[str] = None):
    """Store auth credentials in a profile."""
    config = load_config()
    name = _resolve_profile_name(profile_name)
    profiles = config.setdefault("profiles", {})
    profile = profiles.setdefault(name, {})
    profile["instance_url"] = instance_url.rstrip("/")
    profile["token"] = token
    if user:
        profile["user"] = user
    config["current_profile"] = name
    save_config(config)


def clear_auth(profile_name: Optional[str] = None):
    """Clear token and user from a profile."""
    config = load_config()
    name = _resolve_profile_name(profile_name)
    profile = config.get("profiles", {}).get(name, {})
    profile.pop("token", None)
    profile.pop("user", None)
    save_config(config)


def list_profiles() -> list[dict]:
    """List all profiles with metadata."""
    config = load_config()
    current = config.get("current_profile", "default")
    profiles = config.get("profiles", {})
    result = []
    for name, data in profiles.items():
        result.append({
            "name": name,
            "instance_url": data.get("instance_url", ""),
            "user": (data.get("user", {}) or {}).get("email", ""),
            "active": name == current,
        })
    return result


def set_current_profile(name: str) -> bool:
    """Switch to a different profile. Returns False if profile doesn't exist."""
    config = load_config()
    if name not in config.get("profiles", {}):
        return False
    config["current_profile"] = name
    save_config(config)
    return True


def remove_profile(name: str) -> bool:
    """Remove a profile. Returns False if it doesn't exist."""
    config = load_config()
    profiles = config.get("profiles", {})
    if name not in profiles:
        return False
    del profiles[name]
    # If we removed the active profile, switch to first remaining (or clear)
    if config.get("current_profile") == name:
        config["current_profile"] = next(iter(profiles), "default")
    save_config(config)
    return True


def profile_name_from_url(url: str) -> str:
    """Derive a profile name from an instance URL (uses hostname)."""
    parsed = urlparse(url)
    hostname = parsed.hostname or "default"
    # Use just hostname, stripping port
    return hostname
