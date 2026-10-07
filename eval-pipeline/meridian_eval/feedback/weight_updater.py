"""Redis routing weight updater.

Reads current provider weights for a route, applies a bounded delta derived
from the eval score, and writes them back atomically via a Lua script.

Rules (ADR-005):
  - Maximum shift per window: 10% (configurable)
  - Minimum floor per provider: 5% — quality alone cannot permanently cut a provider
  - Weights must sum to 1.0 after the update
  - All reads + writes are atomic (EVALSHA) to prevent races with the gateway router
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Lua script: atomically read all weights for a route, apply deltas, re-normalise,
# enforce floor, and write back.  Keys[1] = hash key pattern prefix.
# ARGV layout: max_shift, min_floor, n_providers, then pairs of (provider, delta).
_LUA_SCRIPT = """
local prefix = KEYS[1]
local max_shift = tonumber(ARGV[1])
local min_floor = tonumber(ARGV[2])
local n = tonumber(ARGV[3])
local updates = {}
for i = 1, n do
    local provider = ARGV[3 + (i-1)*2 + 1]
    local delta    = tonumber(ARGV[3 + (i-1)*2 + 2])
    updates[provider] = delta
end

-- Read current weights (default 1/n if missing)
local weights = {}
local total_providers = 0
for provider, _ in pairs(updates) do
    total_providers = total_providers + 1
    local raw = redis.call('HGET', prefix, provider)
    weights[provider] = raw and tonumber(raw) or (1.0 / total_providers)
end

-- Apply clamped deltas
for provider, delta in pairs(updates) do
    local clamped = math.max(-max_shift, math.min(max_shift, delta))
    weights[provider] = math.max(min_floor, weights[provider] + clamped)
end

-- Re-normalise so weights sum to 1.0
local total = 0
for _, w in pairs(weights) do total = total + w end
for provider, w in pairs(weights) do
    weights[provider] = w / total
end

-- Write back
for provider, w in pairs(weights) do
    redis.call('HSET', prefix, provider, tostring(w))
end

return redis.status_reply('OK')
"""


class WeightUpdater:
    """Applies bounded, normalised weight deltas to the Redis routing table."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        max_shift: float = 0.10,
        min_floor: float = 0.05,
        key_prefix: str = "meridian:weights",
    ) -> None:
        self._redis = redis_client
        self._max_shift = max_shift
        self._min_floor = min_floor
        self._key_prefix = key_prefix
        self._script_sha: str | None = None

    async def _ensure_script(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(_LUA_SCRIPT)
        return self._script_sha

    def _route_key(self, route_id: str) -> str:
        return f"{self._key_prefix}:{route_id}"

    async def apply(self, route_id: str, deltas: dict[str, float]) -> None:
        """Apply weight deltas for ``route_id``.

        ``deltas`` maps provider name → signed delta (e.g. {"anthropic": 0.05}).
        Positive delta = increase weight; negative = decrease.
        """
        if not deltas:
            return

        sha = await self._ensure_script()
        key = self._route_key(route_id)
        n = len(deltas)
        argv = [str(self._max_shift), str(self._min_floor), str(n)]
        for provider, delta in deltas.items():
            argv.extend([provider, str(delta)])

        try:
            await self._redis.evalsha(sha, 1, key, *argv)
            logger.info(
                "weights updated",
                extra={"route_id": route_id, "deltas": deltas},
            )
        except aioredis.NoScriptError:
            # Redis restarted and lost the cached script — reload and retry once.
            self._script_sha = None
            sha = await self._ensure_script()
            await self._redis.evalsha(sha, 1, key, *argv)
