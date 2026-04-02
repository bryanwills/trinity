"""Profile management commands: list, use, remove."""

import click

from ..config import list_profiles, remove_profile, set_current_profile
from ..output import format_output


@click.group()
def profile():
    """Manage instance profiles.

    Profiles let you store credentials for multiple Trinity instances
    and switch between them.
    """
    pass


@profile.command("list")
@click.option("--format", "fmt", type=click.Choice(["json", "table"]), default="table",
              help="Output format")
def profile_list(fmt):
    """List all configured profiles."""
    profiles = list_profiles()
    if not profiles:
        click.echo("No profiles configured. Run 'trinity init' to create one.")
        return

    if fmt == "json":
        format_output(profiles, "json")
    else:
        for p in profiles:
            marker = "*" if p["active"] else " "
            user_str = f" ({p['user']})" if p["user"] else ""
            click.echo(f"  {marker} {p['name']:20s} {p['instance_url']}{user_str}")


@profile.command("use")
@click.argument("name")
def profile_use(name):
    """Switch to a different profile."""
    if set_current_profile(name):
        click.echo(f"Switched to profile '{name}'")
    else:
        click.echo(f"Profile '{name}' not found. Run 'trinity profile list' to see available profiles.", err=True)
        raise SystemExit(1)


@profile.command("remove")
@click.argument("name")
def profile_remove(name):
    """Remove a profile."""
    if remove_profile(name):
        click.echo(f"Removed profile '{name}'")
    else:
        click.echo(f"Profile '{name}' not found.", err=True)
        raise SystemExit(1)
