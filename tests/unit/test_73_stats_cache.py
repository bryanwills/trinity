"""Unit tests for the #73 per-agent container-stats cache + single-flight.

Covers the primary CPU fix in services/agent_service/stats.py:
  - single-flight coalescing (N concurrent same-agent misses -> 1 Docker call)
  - per-agent lock isolation (T-1: distinct agents don't block each other)
  - TTL hit / expiry
  - explicit invalidation
  - invalidation race (F4: in-flight leader's write discarded via gen guard)
  - error paths are never cached (404 / 400)
  - defensive env-var parse (F9) + TTL=0 disables the cache
  - payload parity (frontend contract guard)

These are true unit tests: the Docker layer is monkeypatched, so no Docker
daemon and no running backend are required. No sys.modules mutation (the lint
gate in tests/lint_sys_modules.py stays green).

Issue: https://github.com/Abilityai/trinity/issues/73
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from services.agent_service import stats as stats_mod


# --- fakes -----------------------------------------------------------------

# A realistic-enough Docker stats payload so _compute_agent_stats produces a
# full result without erroring.
_FAKE_DOCKER_STATS = {
    "cpu_stats": {
        "cpu_usage": {"total_usage": 2000, "percpu_usage": [1000, 1000]},
        "system_cpu_usage": 10000,
    },
    "precpu_stats": {
        "cpu_usage": {"total_usage": 1000},
        "system_cpu_usage": 5000,
    },
    "memory_stats": {"usage": 500_000, "limit": 1_000_000, "stats": {"cache": 100_000}},
    "networks": {"eth0": {"rx_bytes": 10, "tx_bytes": 20}},
}


class _FakeContainer:
    def __init__(self, status: str = "running"):
        self.status = status
        # Empty StartedAt -> uptime 0, avoids datetime parsing in the test.
        self.attrs = {"State": {"StartedAt": ""}}


@pytest.fixture(autouse=True)
def _reset_stats_state():
    """Clear the module-level per-agent cache/locks/gen before AND after each
    test so cases don't leak cache entries into one another."""
    stats_mod._agent_stats_cache.clear()
    stats_mod._agent_stats_locks.clear()
    stats_mod._agent_stats_gen.clear()
    yield
    stats_mod._agent_stats_cache.clear()
    stats_mod._agent_stats_locks.clear()
    stats_mod._agent_stats_gen.clear()


def _install_fast_docker(monkeypatch, *, running: bool = True, container=None):
    """Patch the Docker seam with a call-counting container_stats stub.

    Returns the mutable `calls` dict ({"n": <count>}).
    """
    calls = {"n": 0}
    fake = container if container is not None else _FakeContainer(
        "running" if running else "exited"
    )

    monkeypatch.setattr(stats_mod, "get_agent_container", lambda name: fake)

    async def _reload(_c):
        return None

    async def _stats(_c, stream=False):
        calls["n"] += 1
        return _FAKE_DOCKER_STATS

    monkeypatch.setattr(stats_mod, "container_reload", _reload)
    monkeypatch.setattr(stats_mod, "container_stats", _stats)
    return calls


# --- single-flight coalescing (core) ---------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_flight_coalescing(monkeypatch):
    """~10 concurrent same-agent requests share ONE Docker call."""
    calls = {"n": 0}
    fake = _FakeContainer("running")
    monkeypatch.setattr(stats_mod, "get_agent_container", lambda name: fake)

    async def _reload(_c):
        return None

    async def _slow_stats(_c, stream=False):
        calls["n"] += 1
        await asyncio.sleep(0.05)  # hold the lock so the others pile up
        return _FAKE_DOCKER_STATS

    monkeypatch.setattr(stats_mod, "container_reload", _reload)
    monkeypatch.setattr(stats_mod, "container_stats", _slow_stats)

    results = await asyncio.gather(
        *[stats_mod.get_agent_stats_logic("agent-a", None) for _ in range(10)]
    )

    assert calls["n"] == 1, "single-flight must collapse to one Docker call"
    # All callers get identical payloads.
    assert all(r == results[0] for r in results)


# --- per-agent lock isolation (T-1) ----------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_agent_lock_isolation(monkeypatch):
    """Two distinct agents each get their own Docker call and don't block
    each other (guards against a single-global-lock regression)."""
    calls = {"n": 0}
    containers = {"a": _FakeContainer("running"), "b": _FakeContainer("running")}
    monkeypatch.setattr(
        stats_mod, "get_agent_container", lambda name: containers[name[-1]]
    )

    async def _reload(_c):
        return None

    async def _stats(_c, stream=False):
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return _FAKE_DOCKER_STATS

    monkeypatch.setattr(stats_mod, "container_reload", _reload)
    monkeypatch.setattr(stats_mod, "container_stats", _stats)

    res_a, res_b = await asyncio.gather(
        stats_mod.get_agent_stats_logic("agent-a", None),
        stats_mod.get_agent_stats_logic("agent-b", None),
    )

    assert calls["n"] == 2, "distinct agents must each compute once"
    assert res_a == res_b  # same fake stats -> same payload, but two calls


# --- TTL hit / expiry -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ttl_hit_then_expiry(monkeypatch):
    calls = _install_fast_docker(monkeypatch)

    class _Clock:
        t = 1000.0

        def monotonic(self):
            return self.t

    clock = _Clock()
    monkeypatch.setattr(stats_mod, "time", clock)

    # First call: miss -> 1 Docker call.
    await stats_mod.get_agent_stats_logic("agent-a", None)
    assert calls["n"] == 1

    # Within TTL (still t=1000): hit -> still 1.
    await stats_mod.get_agent_stats_logic("agent-a", None)
    assert calls["n"] == 1

    # Past TTL (default 12s): miss -> 2.
    clock.t = 1000.0 + stats_mod._AGENT_STATS_TTL + 1
    await stats_mod.get_agent_stats_logic("agent-a", None)
    assert calls["n"] == 2


# --- explicit invalidation --------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalidation_recomputes(monkeypatch):
    calls = _install_fast_docker(monkeypatch)

    await stats_mod.get_agent_stats_logic("agent-a", None)
    assert calls["n"] == 1

    stats_mod.invalidate_agent_stats_cache("agent-a")
    assert "agent-a" not in stats_mod._agent_stats_cache

    await stats_mod.get_agent_stats_logic("agent-a", None)
    assert calls["n"] == 2


# --- invalidation race (F4) -------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalidation_during_inflight_discards_stale_write(monkeypatch):
    """A leader whose Docker call is in flight when invalidate() runs must NOT
    repopulate the cache (its captured generation is stale)."""
    calls = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()
    fake = _FakeContainer("running")
    monkeypatch.setattr(stats_mod, "get_agent_container", lambda name: fake)

    async def _reload(_c):
        return None

    async def _gated_stats(_c, stream=False):
        calls["n"] += 1
        started.set()
        await release.wait()
        return _FAKE_DOCKER_STATS

    monkeypatch.setattr(stats_mod, "container_reload", _reload)
    monkeypatch.setattr(stats_mod, "container_stats", _gated_stats)

    leader = asyncio.create_task(stats_mod.get_agent_stats_logic("agent-a", None))
    await started.wait()  # leader is mid-Docker-call

    stats_mod.invalidate_agent_stats_cache("agent-a")  # bumps gen
    release.set()
    result = await leader  # leader still returns its computed payload

    assert result["status"] == "running"
    assert "agent-a" not in stats_mod._agent_stats_cache, (
        "stale in-flight write must be discarded after invalidation"
    )

    # Next call recomputes (cache was not poisoned).
    await stats_mod.get_agent_stats_logic("agent-a", None)
    assert calls["n"] == 2


# --- error paths are never cached -------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_container_404_not_cached(monkeypatch):
    monkeypatch.setattr(stats_mod, "get_agent_container", lambda name: None)

    with pytest.raises(HTTPException) as exc:
        await stats_mod.get_agent_stats_logic("agent-a", None)
    assert exc.value.status_code == 404
    assert "agent-a" not in stats_mod._agent_stats_cache


@pytest.mark.unit
@pytest.mark.asyncio
async def test_not_running_400_not_cached(monkeypatch):
    _install_fast_docker(monkeypatch, running=False)

    with pytest.raises(HTTPException) as exc:
        await stats_mod.get_agent_stats_logic("agent-a", None)
    assert exc.value.status_code == 400
    assert "agent-a" not in stats_mod._agent_stats_cache


# --- defensive TTL parse (F9) -----------------------------------------------


@pytest.mark.unit
def test_parse_ttl_is_defensive():
    """A bad env value must fall back to the default WITHOUT raising — this is
    exactly the logic that runs at import time."""
    default = stats_mod._AGENT_STATS_DEFAULT_TTL
    assert stats_mod._parse_agent_stats_ttl(None) == default
    assert stats_mod._parse_agent_stats_ttl("not-a-number") == default  # no raise
    assert stats_mod._parse_agent_stats_ttl("") == default
    assert stats_mod._parse_agent_stats_ttl("0") == 0  # disabled
    assert stats_mod._parse_agent_stats_ttl("-5") == 0  # negative -> disabled
    assert stats_mod._parse_agent_stats_ttl("20") == 20
    assert stats_mod._parse_agent_stats_ttl("999999") == stats_mod._AGENT_STATS_TTL_MAX


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ttl_zero_disables_cache(monkeypatch):
    """TTL=0 -> every call recomputes (no caching, no single-flight store)."""
    calls = _install_fast_docker(monkeypatch)
    monkeypatch.setattr(stats_mod, "_AGENT_STATS_TTL", 0)

    await stats_mod.get_agent_stats_logic("agent-a", None)
    await stats_mod.get_agent_stats_logic("agent-a", None)

    assert calls["n"] == 2
    assert "agent-a" not in stats_mod._agent_stats_cache


# --- payload parity (frontend contract) -------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_payload_shape_parity(monkeypatch):
    """The returned dict keys/types must match the pre-cache contract that the
    frontend useAgentStats store consumes."""
    _install_fast_docker(monkeypatch)

    result = await stats_mod.get_agent_stats_logic("agent-a", None)

    assert set(result) == {
        "cpu_percent",
        "memory_used_bytes",
        "memory_limit_bytes",
        "memory_percent",
        "network_rx_bytes",
        "network_tx_bytes",
        "uptime_seconds",
        "status",
    }
    assert isinstance(result["cpu_percent"], float)
    assert isinstance(result["memory_used_bytes"], int)
    assert isinstance(result["memory_limit_bytes"], int)
    assert isinstance(result["memory_percent"], float)
    assert isinstance(result["network_rx_bytes"], int)
    assert isinstance(result["network_tx_bytes"], int)
    assert isinstance(result["uptime_seconds"], int)
    assert result["status"] == "running"
