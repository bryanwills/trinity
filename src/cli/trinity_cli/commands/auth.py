"""Authentication commands: login, logout, status, init."""

import click

from ..client import TrinityClient, TrinityAPIError
from ..config import (
    clear_auth, get_instance_url, get_user, load_config,
    profile_name_from_url, set_auth, _resolve_profile_name,
)


def _get_profile_name(ctx: click.Context) -> str | None:
    """Extract the --profile value from the root context."""
    root = ctx.find_root()
    return root.obj.get("profile") if root.obj else None


@click.command()
@click.option("--instance", help="Trinity instance URL (e.g. https://trinity.example.com)")
@click.option("--profile", "profile_opt", default=None,
              help="Profile name to store credentials under (default: hostname)")
@click.pass_context
def login(ctx, instance, profile_opt):
    """Log in to a Trinity instance with email verification."""
    profile_name = profile_opt or _get_profile_name(ctx)
    url = instance or get_instance_url(profile_name)
    if not url:
        url = click.prompt("Trinity instance URL")
    url = url.rstrip("/")

    client = TrinityClient(base_url=url, token="none")

    email = click.prompt("Email")

    # Request verification code
    try:
        client.post_unauthenticated("/api/auth/email/request", {"email": email})
    except TrinityAPIError as e:
        click.echo(f"Error requesting code: {e.detail}", err=True)
        raise SystemExit(1)

    click.echo(f"Verification code sent to {email}")
    code = click.prompt("Enter 6-digit code")

    # Verify code and get token
    try:
        result = client.post_unauthenticated("/api/auth/email/verify", {
            "email": email,
            "code": code,
        })
    except TrinityAPIError as e:
        click.echo(f"Verification failed: {e.detail}", err=True)
        raise SystemExit(1)

    token = result["access_token"]
    user = result.get("user")

    # Determine profile name: explicit > global flag > derive from URL
    target_profile = profile_name or profile_name_from_url(url)
    set_auth(url, token, user, profile_name=target_profile)
    name = user.get("name") or user.get("email") or user.get("username") if user else email
    click.echo(f"Logged in as {name} [profile: {target_profile}]")


@click.command()
@click.pass_context
def logout(ctx):
    """Clear stored credentials for the current profile."""
    profile_name = _get_profile_name(ctx)
    clear_auth(profile_name)
    resolved = _resolve_profile_name(profile_name)
    click.echo(f"Logged out [profile: {resolved}]")


@click.command()
@click.pass_context
def status(ctx):
    """Show current login status and instance info."""
    profile_name = _get_profile_name(ctx)
    resolved = _resolve_profile_name(profile_name)
    url = get_instance_url(profile_name)

    click.echo(f"Profile:  {resolved}")

    if not url:
        click.echo("Instance: Not configured. Run 'trinity init' or 'trinity login'.")
        return

    user = get_user(profile_name)
    config = load_config()
    profile_data = config.get("profiles", {}).get(resolved, {})

    click.echo(f"Instance: {url}")
    if user:
        click.echo(f"User:     {user.get('email') or user.get('username')}")
        click.echo(f"Role:     {user.get('role', 'unknown')}")
    elif profile_data.get("token"):
        click.echo("User:     (API key auth)")
    else:
        click.echo("User:     Not logged in")

    # Check connectivity
    try:
        client = TrinityClient(base_url=url, token=profile_data.get("token", "none"))
        client.get_unauthenticated("/api/auth/mode")
        click.echo("Status:   Connected")
    except Exception:
        click.echo("Status:   Unreachable")


@click.command()
@click.option("--profile", "profile_opt", default=None,
              help="Profile name (default: derived from instance hostname)")
@click.pass_context
def init(ctx, profile_opt):
    """Set up Trinity CLI: configure instance, request access, and log in.

    One command to go from zero to authenticated. Creates a named profile
    for the instance (defaults to hostname).
    """
    url = click.prompt("Trinity instance URL", default="http://localhost:8000")
    url = url.rstrip("/")

    client = TrinityClient(base_url=url, token="none")

    # Verify instance is reachable
    try:
        client.get_unauthenticated("/api/auth/mode")
    except Exception:
        click.echo(f"Cannot reach {url}. Check the URL and try again.", err=True)
        raise SystemExit(1)

    click.echo(f"Connected to {url}")

    # Determine profile name
    profile_name = profile_opt or _get_profile_name(ctx) or profile_name_from_url(url)

    email = click.prompt("Email")

    # Request access (auto-approve endpoint)
    try:
        client.post_unauthenticated("/api/access/request", {"email": email})
        click.echo("Access granted")
    except TrinityAPIError as e:
        if e.status_code == 409:
            click.echo("Already registered")
        else:
            click.echo(f"Access request failed: {e.detail}", err=True)
            raise SystemExit(1)

    # Send verification code
    try:
        client.post_unauthenticated("/api/auth/email/request", {"email": email})
    except TrinityAPIError as e:
        click.echo(f"Error requesting code: {e.detail}", err=True)
        raise SystemExit(1)

    click.echo(f"Verification code sent to {email}")
    code = click.prompt("Enter 6-digit code")

    # Verify and get token
    try:
        result = client.post_unauthenticated("/api/auth/email/verify", {
            "email": email,
            "code": code,
        })
    except TrinityAPIError as e:
        click.echo(f"Verification failed: {e.detail}", err=True)
        raise SystemExit(1)

    token = result["access_token"]
    user = result.get("user")

    set_auth(url, token, user, profile_name=profile_name)
    name = user.get("name") or user.get("email") or user.get("username") if user else email
    click.echo(f"Logged in as {name} [profile: {profile_name}]")
    click.echo(f"\nTrinity CLI is ready. Try 'trinity agents list'.")
