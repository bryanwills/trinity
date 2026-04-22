"""
Regression tests for #458 — `initialize_git_in_container` .gitignore handling.

The bug: the old code ran `cat > .gitignore <<EOF ...` which clobbered any
workspace-supplied `.gitignore` (so `/trinity:onboard`'s ignore rules were
lost) and listed only shell/cache entries (so `.env` and `.mcp.json`, which
`inject_credentials` writes, were never ignored and got committed on the
initial sync). The fix replaces that truncate-and-write with an
append-if-missing merge that preserves existing rules and adds the three
patterns the reporter named.

These tests exercise the actual bash script returned by
`_build_gitignore_merge_command` against a temp directory, which is honest:
the only difference from production is the host filesystem vs. the agent
container.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


_project_root = Path(__file__).resolve().parents[2]
backend_path = str(_project_root / "src" / "backend")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)


def _load_git_service():
    """Import git_service with heavy dependencies mocked out."""
    mock_modules = {}
    for mod in [
        "docker", "docker.errors", "docker.types",
        "redis", "redis.asyncio",
        "database",
        "services.docker_service",
    ]:
        mock_modules[mod] = Mock()
    mock_modules["database"].db = Mock()
    mock_modules["database"].AgentGitConfig = Mock
    mock_modules["database"].GitSyncResult = Mock

    with patch.dict("sys.modules", mock_modules):
        for key in list(sys.modules.keys()):
            if key.startswith("services.git_service"):
                del sys.modules[key]
        import services.git_service as gs
    return gs


def _run_merge(tmp_path: Path) -> str:
    """Run the real merge command (produced by `_build_gitignore_merge_command`)
    against ``tmp_path`` and return the resulting `.gitignore` contents.
    """
    gs = _load_git_service()
    # The production helper hardcodes the path that the agent container
    # passes in; for tests we just point it at the temp dir.
    cmd = gs._build_gitignore_merge_command(str(tmp_path))
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, (
        f"merge command failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    return (tmp_path / ".gitignore").read_text()


def test_preserves_preexisting_gitignore(tmp_path):
    """A workspace `.gitignore` with user rules must survive the merge.

    Regression guard for the primary #458 bug: the old `cat > .gitignore`
    path clobbered anything `/trinity:onboard` (or the user) had written.
    """
    preexisting = "# user rules\nnode_modules/\nbuild/\n*.log\n"
    (tmp_path / ".gitignore").write_text(preexisting)

    content = _run_merge(tmp_path)

    # Every user line still present, verbatim.
    for line in ("# user rules", "node_modules/", "build/", "*.log"):
        assert line in content.splitlines(), (
            f"user rule {line!r} lost — got:\n{content}"
        )
    # And the three credential patterns the reporter named are now covered.
    for p in (".env", ".env.*", ".mcp.json"):
        assert p in content.splitlines(), (
            f"credential pattern {p!r} missing after merge — got:\n{content}"
        )


def test_fresh_agent_ignores_env_and_mcp_json(tmp_path):
    """With no pre-existing `.gitignore`, the merge must produce one that
    ignores `.env`, `.env.*`, and `.mcp.json` — the files `inject_credentials`
    writes and that #458 observed leaking on the initial commit.
    """
    assert not (tmp_path / ".gitignore").exists()

    content = _run_merge(tmp_path)
    lines = content.splitlines()

    for p in (".env", ".env.*", ".mcp.json"):
        assert p in lines, (
            f"pattern {p!r} not in .gitignore after fresh merge — got:\n{content}"
        )
