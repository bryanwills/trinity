"""
Agent Service Stats - Container and context statistics.

Handles fetching context window and container stats.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException

from models import User
from database import db
from services.docker_service import get_agent_container
from services.docker_utils import container_reload, container_stats
from .helpers import get_accessible_agents

logger = logging.getLogger(__name__)

# PERF-269: In-memory cache for context stats (15s TTL)
_context_stats_cache = {
    "data": None,
    "timestamp": 0,
    "ttl": 15  # seconds
}


def invalidate_context_stats_cache():
    """Invalidate the context stats cache (call on agent start/stop)."""
    _context_stats_cache["data"] = None
    _context_stats_cache["timestamp"] = 0


# ---------------------------------------------------------------------------
# #73: per-agent container-stats cache + single-flight coalescing.
#
# `container.stats(stream=False)` costs ~1-2s of Docker double-sampling and is
# funnelled through a 4-worker thread pool. N agents x multiple tabs/users
# polling /stats every 10s was the dominant backend CPU sink. We mirror the
# PERF-269 context-stats cache, but PER-AGENT and with single-flight so
# concurrent requests for the same agent share one Docker call instead of N.
#
# TTL default is 12s (above the frontend's 10s /stats poll so a lone viewer's
# repeat polls also hit cache). HONEST FRESHNESS TRADE (F3): with a fixed 10s
# poll and 12s TTL, fresh Docker samples land ~every 20s and the gauge shows a
# duplicate sparkline point on alternating ticks — an accepted cosmetic cost on
# an already-coarse CPU/mem gauge. No single TTL gives both a smooth 10s chart
# and full per-tab dedup for a fixed-interval poller; the steady-state
# Docker-load cut is the goal.
#
# This cache is in-process and PER-WORKER (matching the context-stats cache):
# explicit lifecycle invalidation clears only the handling worker's cache, so
# the TTL is the staleness bound for transitions it can't see (crashes,
# external docker ops, the sibling uvicorn worker). Errors (404/400/500) are
# never cached, so a failing agent never gets pinned for the TTL.
# ---------------------------------------------------------------------------
_AGENT_STATS_DEFAULT_TTL = 12  # seconds (PERF-1)
_AGENT_STATS_TTL_MAX = 300  # clamp ceiling — 5 min is already absurdly stale


def _parse_agent_stats_ttl(raw: str | None) -> int:
    """Defensively parse AGENT_STATS_CACHE_TTL_SECONDS (F9).

    A bad env value must NEVER crash backend startup. Returns the default on
    any parse failure, clamps to [0, _AGENT_STATS_TTL_MAX], and treats 0 as
    "cache disabled" (debugging aid — every call recomputes).
    """
    if raw is None:
        return _AGENT_STATS_DEFAULT_TTL
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid AGENT_STATS_CACHE_TTL_SECONDS=%r; using default %ss",
            raw, _AGENT_STATS_DEFAULT_TTL,
        )
        return _AGENT_STATS_DEFAULT_TTL
    if value < 0:
        return 0  # negative is meaningless → treat as disabled
    return min(value, _AGENT_STATS_TTL_MAX)


# Parsed once at import (env-overridable, defensively parsed). Read through the
# module global in get_agent_stats_logic so tests can monkeypatch it.
_AGENT_STATS_TTL = _parse_agent_stats_ttl(os.getenv("AGENT_STATS_CACHE_TTL_SECONDS"))

# {agent_name: {"data": <stats dict>, "timestamp": <monotonic>}}
_agent_stats_cache: dict = {}
# {agent_name: asyncio.Lock} — per-agent single-flight (setdefault on demand).
_agent_stats_locks: dict = {}
# {agent_name: int} — generation counter (F4). invalidate_* bumps it; the
# single-flight leader writes its result only if the gen is unchanged after the
# Docker call, discarding a stale write that an invalidation raced past.
#
# NOTE on growth: this dict retains one int per distinct agent name ever seen
# (invalidate must BUMP, never pop — popping would let a leader whose captured
# gen happened to be 0 match the default-0 and repopulate a just-deleted
# agent's cache, reintroducing the exact F4 race). Retention is therefore
# bounded by fleet size, not unbounded, and the per-name cost is a single int —
# accepted by design rather than traded against F4 correctness.
_agent_stats_gen: dict = {}


def invalidate_agent_stats_cache(agent_name: str) -> None:
    """#73: drop a single agent's cached stats + its single-flight lock, and
    bump its generation (F4) so any in-flight leader's later write is
    discarded. Call on agent start/stop/delete (parallel to
    invalidate_context_stats_cache)."""
    _agent_stats_cache.pop(agent_name, None)
    _agent_stats_locks.pop(agent_name, None)
    _agent_stats_gen[agent_name] = _agent_stats_gen.get(agent_name, 0) + 1


async def _fetch_single_agent_context(agent: dict, client: httpx.AsyncClient) -> dict:
    """Fetch context stats for a single agent (used for concurrent fetching)."""
    agent_name = agent["name"]
    status = agent["status"]

    # Initialize default stats
    stats = {
        "name": agent_name,
        "status": status,
        "activityState": "offline",
        "contextPercent": 0,
        "contextUsed": 0,
        "contextMax": 200000,
        "lastActivityTime": None
    }

    # Only fetch context stats for running agents
    if status != "running":
        return stats

    # Fetch context stats from agent's internal API
    try:
        container = get_agent_container(agent_name)
        if container:
            agent_url = f"http://{container.name}:8000/api/chat/session"
            response = await client.get(agent_url)
            if response.status_code == 200:
                session_data = response.json()
                stats["contextPercent"] = session_data.get("context_percent", 0)
                stats["contextUsed"] = session_data.get("context_tokens", 0)
                stats["contextMax"] = session_data.get("context_window", 200000)
    except Exception as e:
        logger.debug(f"Error fetching context stats for {agent_name}: {e}")

    # Determine active/idle state based on recent activity
    try:
        cutoff_time = (datetime.utcnow() - timedelta(seconds=60)).isoformat()
        recent_activities = db.get_agent_activities(
            agent_name=agent_name,
            limit=1
        )

        if recent_activities and len(recent_activities) > 0:
            last_activity = recent_activities[0]
            activity_time = last_activity.get("created_at")
            stats["lastActivityTime"] = activity_time

            if activity_time and activity_time > cutoff_time:
                if last_activity.get("activity_state") == "started":
                    stats["activityState"] = "active"
                else:
                    stats["activityState"] = "idle"
            else:
                stats["activityState"] = "idle"
        else:
            stats["activityState"] = "idle"
    except Exception as e:
        logger.debug(f"Error determining activity state for {agent_name}: {e}")
        stats["activityState"] = "idle"

    return stats


async def get_agents_context_stats_logic(
    current_user: User
) -> dict:
    """
    Get context window stats and activity state for all accessible agents.

    PERF-269: Results are cached for 15 seconds to avoid repeated N+1 HTTP
    fan-out to every running agent container. Cache is invalidated on
    agent start/stop events.

    Returns: List of agent stats with context usage and active/idle/offline state
    """
    now = time.monotonic()

    # Check cache (PERF-269)
    if (_context_stats_cache["data"] is not None
            and (now - _context_stats_cache["timestamp"]) < _context_stats_cache["ttl"]):
        # Return cached data, filtered to accessible agents for this user
        accessible_names = {a["name"] for a in get_accessible_agents(current_user)}
        cached = _context_stats_cache["data"]
        filtered = [s for s in cached if s["name"] in accessible_names]
        return {"agents": filtered}

    accessible_agents = get_accessible_agents(current_user)

    # PERF-269: Only fan out HTTP calls to running agents (stopped agents return defaults)
    running_agents = [a for a in accessible_agents if a.get("status") == "running"]
    stopped_agents = [a for a in accessible_agents if a.get("status") != "running"]

    # Build default stats for stopped agents (no HTTP call needed)
    stopped_stats = [{
        "name": a["name"],
        "status": a["status"],
        "activityState": "offline",
        "contextPercent": 0,
        "contextUsed": 0,
        "contextMax": 200000,
        "lastActivityTime": None
    } for a in stopped_agents]

    # Fetch running agent stats concurrently using a shared client
    running_stats = []
    if running_agents:
        async with httpx.AsyncClient(timeout=2.0) as client:
            tasks = [_fetch_single_agent_context(agent, client) for agent in running_agents]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.debug(f"Error fetching stats for agent: {result}")
                agent = running_agents[i]
                running_stats.append({
                    "name": agent["name"],
                    "status": agent["status"],
                    "activityState": "idle",
                    "contextPercent": 0,
                    "contextUsed": 0,
                    "contextMax": 200000,
                    "lastActivityTime": None
                })
            else:
                running_stats.append(result)

    all_stats = running_stats + stopped_stats

    # Update cache (PERF-269)
    _context_stats_cache["data"] = all_stats
    _context_stats_cache["timestamp"] = now

    return {"agents": all_stats}


async def _compute_agent_stats(agent_name: str) -> dict:
    """Do the actual (expensive) Docker work for one agent's live stats.

    Preserves the original error semantics: 404 when the container is missing,
    400 when it is not running, 500 on any stats/compute failure. Callers must
    NOT cache the result on a raised error (a transient stop/404 must not get
    pinned for the TTL)."""
    container = get_agent_container(agent_name)
    if not container:
        raise HTTPException(status_code=404, detail="Agent not found")

    await container_reload(container)
    if container.status != "running":
        raise HTTPException(status_code=400, detail="Agent is not running")

    try:
        stats = await container_stats(container, stream=False)

        cpu_percent = 0.0
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - \
                    precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - \
                       precpu_stats.get("system_cpu_usage", 0)

        if system_delta > 0 and cpu_delta > 0:
            num_cpus = len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [])) or 1
            cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0

        memory_stats = stats.get("memory_stats", {})
        memory_used = memory_stats.get("usage", 0)
        memory_limit = memory_stats.get("limit", 0)
        cache = memory_stats.get("stats", {}).get("cache", 0)
        memory_used_actual = max(0, memory_used - cache)

        networks = stats.get("networks", {})
        network_rx = sum(net.get("rx_bytes", 0) for net in networks.values())
        network_tx = sum(net.get("tx_bytes", 0) for net in networks.values())

        started_at = container.attrs.get("State", {}).get("StartedAt", "")
        uptime_seconds = 0
        if started_at:
            try:
                start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00").split(".")[0])
                uptime_seconds = int((datetime.now(start_time.tzinfo) - start_time).total_seconds())
            except Exception:
                pass

        return {
            "cpu_percent": round(cpu_percent, 1),
            "memory_used_bytes": memory_used_actual,
            "memory_limit_bytes": memory_limit,
            "memory_percent": round((memory_used_actual / memory_limit * 100) if memory_limit > 0 else 0, 1),
            "network_rx_bytes": network_rx,
            "network_tx_bytes": network_tx,
            "uptime_seconds": uptime_seconds,
            "status": container.status
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


async def get_agent_stats_logic(
    agent_name: str,
    current_user: User
) -> dict:
    """
    Get live container stats (CPU, memory, network) for an agent.

    #73: serves repeat/concurrent requests for the same agent from a short
    TTL cache and collapses concurrent same-agent misses into ONE Docker call
    via per-agent single-flight. Payload shape is byte-for-byte identical to
    the pre-cache version (the frontend useAgentStats store consumes it as-is).
    """
    ttl = _AGENT_STATS_TTL

    # Cache disabled (TTL=0, debugging): always recompute, never store.
    if ttl <= 0:
        return await _compute_agent_stats(agent_name)

    # Fast path: a fresh cache entry short-circuits all Docker work.
    entry = _agent_stats_cache.get(agent_name)
    if entry is not None and (time.monotonic() - entry["timestamp"]) < ttl:
        return entry["data"]

    # Miss path: single-flight on a per-agent lock so concurrent same-agent
    # requests share one Docker call. setdefault has no await, so it is atomic
    # w.r.t. other coroutines — all same-agent callers get the same Lock.
    lock = _agent_stats_locks.setdefault(agent_name, asyncio.Lock())
    async with lock:
        # Double-checked locking: a request that waited on the lock finds the
        # entry the leader just populated and returns it without a Docker call.
        entry = _agent_stats_cache.get(agent_name)
        if entry is not None and (time.monotonic() - entry["timestamp"]) < ttl:
            return entry["data"]

        # Capture the generation BEFORE the Docker call (F4). If an
        # invalidation bumps it while we await, we discard this (now stale)
        # result instead of repopulating the cache.
        gen = _agent_stats_gen.get(agent_name, 0)
        data = await _compute_agent_stats(agent_name)  # raises → not cached
        if _agent_stats_gen.get(agent_name, 0) == gen:
            _agent_stats_cache[agent_name] = {
                "data": data,
                "timestamp": time.monotonic(),
            }
        return data
