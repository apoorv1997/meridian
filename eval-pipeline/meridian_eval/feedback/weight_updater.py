"""Redis routing weight updater.

Reads every provider weight on a route, applies bounded deltas derived from eval
scores, and writes the result back atomically in one Lua script, so the gateway
router never reads a half-updated route.

Rules (ADR-005):
  - Each provider's delta is clamped to +/- max_shift (default 10%) per window.
  - Weights on the route always sum to 1.0, including providers that got no delta.
  - Every provider keeps at least min_floor (default 5%) after renormalising, so
    quality alone never cuts a provider off. If the floor cannot hold for every
    provider (n * min_floor >= 1), all providers get an equal share.
  - A provider with no stored weight starts at an equal share of the route.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from redis.exceptions import NoScriptError

logger = logging.getLogger(__name__)


def route_weights_key(tenant_id: str, route_id: str) -> str:
    """Redis hash holding one tenant's provider weights for one route.

    Route IDs are only unique within a tenant, so the tenant is part of the key.
    Must match weightsKey() in gateway/internal/router/weighted.go, which reads it.
    """
    return f"route:{tenant_id}:{route_id}:weights"


# KEYS[1] = route weights hash.
# ARGV = max_shift, min_floor, n, then n pairs of (provider, delta).
# Returns a flat array of provider, weight pairs as written.
_LUA_SCRIPT = """
local key = KEYS[1]
local max_shift = tonumber(ARGV[1])
local min_floor = tonumber(ARGV[2])
local n_updates = tonumber(ARGV[3])

local deltas = {}
for i = 1, n_updates do
    deltas[ARGV[2 + 2 * i]] = tonumber(ARGV[3 + 2 * i])
end

-- Every provider on the route: the ones already stored plus any new ones.
local stored = {}
local seen, providers = {}, {}
local raw = redis.call('HGETALL', key)
for i = 1, #raw, 2 do
    local p = raw[i]
    stored[p] = tonumber(raw[i + 1])  -- nil if the stored value isn't a number
    if not seen[p] then seen[p] = true; providers[#providers + 1] = p end
end
for p, _ in pairs(deltas) do
    if not seen[p] then seen[p] = true; providers[#providers + 1] = p end
end
table.sort(providers)  -- deterministic order for the arithmetic below
local n = #providers

local weights = {}
for _, p in ipairs(providers) do
    local w = stored[p] or (1.0 / n)  -- missing or non-numeric: equal share
    local d = deltas[p]
    if d then
        w = w + math.max(-max_shift, math.min(max_shift, d))
    end
    weights[p] = math.max(0.0, w)
end

-- Renormalise so the whole route sums to 1.0.
local total = 0.0
for _, p in ipairs(providers) do total = total + weights[p] end
for _, p in ipairs(providers) do
    if total > 0 then weights[p] = weights[p] / total else weights[p] = 1.0 / n end
end

-- Enforce the floor after renormalising. Raising one provider to the floor takes
-- share from the others, which can push another one under it, so repeat until no
-- provider is newly floored. Ends in at most n rounds.
if min_floor * n >= 1.0 then
    for _, p in ipairs(providers) do weights[p] = 1.0 / n end
else
    local floored = {}
    while true do
        local newly = false
        for _, p in ipairs(providers) do
            if not floored[p] and weights[p] < min_floor then floored[p] = true; newly = true end
        end
        if not newly then break end
        local k, free_total = 0, 0.0
        for _, p in ipairs(providers) do
            if floored[p] then k = k + 1 else free_total = free_total + weights[p] end
        end
        local free_mass = 1.0 - k * min_floor
        for _, p in ipairs(providers) do
            if floored[p] then
                weights[p] = min_floor
            elseif free_total > 0 then
                weights[p] = weights[p] * free_mass / free_total
            end
        end
    end
end

local out = {}
for _, p in ipairs(providers) do
    local w = string.format('%.17g', weights[p])
    redis.call('HSET', key, p, w)
    out[#out + 1] = p
    out[#out + 1] = w
end
return out
"""


class WeightUpdater:
    """Applies bounded, normalised weight deltas to the Redis routing table."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        *,
        max_shift: float = 0.10,
        min_floor: float = 0.05,
    ) -> None:
        self._redis = redis_client
        self._max_shift = max_shift
        self._min_floor = min_floor
        self._script_sha: str | None = None

    async def _ensure_script(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(_LUA_SCRIPT)
        return self._script_sha

    async def apply(
        self, tenant_id: str, route_id: str, deltas: dict[str, float]
    ) -> dict[str, float]:
        """Apply weight deltas to one tenant's route and return the route's new weights.

        ``deltas`` maps provider name to a signed delta (e.g. {"anthropic": 0.05}).
        Positive raises that provider's weight, negative lowers it.
        """
        if not deltas:
            return {}

        key = route_weights_key(tenant_id, route_id)
        argv = [str(self._max_shift), str(self._min_floor), str(len(deltas))]
        for provider, delta in deltas.items():
            argv.extend([provider, str(delta)])

        sha = await self._ensure_script()
        try:
            flat = await self._redis.evalsha(sha, 1, key, *argv)
        except NoScriptError:
            # Redis restarted and lost the cached script: reload it and retry once.
            self._script_sha = None
            sha = await self._ensure_script()
            flat = await self._redis.evalsha(sha, 1, key, *argv)

        weights = {_text(flat[i]): float(_text(flat[i + 1])) for i in range(0, len(flat), 2)}
        logger.info(
            "weights updated",
            extra={
                "tenant_id": tenant_id,
                "route_id": route_id,
                "deltas": deltas,
                "weights": weights,
            },
        )
        return weights


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value
