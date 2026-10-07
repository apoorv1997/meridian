package ratelimit

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// tokenBucketScript atomically checks and deducts tokens from a Redis-backed
// token bucket using Lua to guarantee no TOCTOU race.
//
// KEYS[1] = bucket key (e.g. "rl:tenant:<id>")
// ARGV[1] = capacity (float)
// ARGV[2] = refill_rate tokens/second (float)
// ARGV[3] = current unix timestamp (float, from Go)
// ARGV[4] = tokens requested (float, typically 1)
//
// Returns 1 if allowed, 0 if denied.
var tokenBucketScript = redis.NewScript(`
local key         = KEYS[1]
local capacity    = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now         = tonumber(ARGV[3])
local requested   = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens     = tonumber(data[1]) or capacity
local last_refill = tonumber(data[2]) or now

local elapsed   = math.max(0, now - last_refill)
local refilled  = math.min(capacity, tokens + elapsed * refill_rate)

if refilled >= requested then
    redis.call('HMSET', key, 'tokens', refilled - requested, 'last_refill', now)
    redis.call('EXPIRE', key, 3600)
    return 1
else
    redis.call('HMSET', key, 'tokens', refilled, 'last_refill', now)
    redis.call('EXPIRE', key, 3600)
    return 0
end
`)

// RedisLimiter runs the token bucket check against Redis.
type RedisLimiter struct {
	rdb *redis.Client
}

func NewRedisLimiter(rdb *redis.Client) *RedisLimiter {
	return &RedisLimiter{rdb: rdb}
}

// Allow returns true if the request should be allowed.
// It deducts 1 token from the bucket on success.
func (r *RedisLimiter) Allow(ctx context.Context, tenantID string, cfg BucketConfig) (bool, error) {
	key := fmt.Sprintf("rl:tenant:%s", tenantID)
	now := float64(time.Now().UnixNano()) / 1e9

	result, err := tokenBucketScript.Run(ctx, r.rdb,
		[]string{key},
		cfg.Capacity,
		cfg.RefillRate,
		now,
		1.0,
	).Int()
	if err != nil {
		return false, fmt.Errorf("ratelimit: redis eval: %w", err)
	}
	return result == 1, nil
}
