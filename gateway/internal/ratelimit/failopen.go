package ratelimit

import (
	"context"
	"log/slog"

	"github.com/redis/go-redis/v9"

	"github.com/apoorv1997/meridian/gateway/internal/metrics"
)

// FailOpenLimiter wraps RedisLimiter with fail-open behaviour per ADR-007:
// if Redis is unavailable, the request is allowed through and logged.
type FailOpenLimiter struct {
	inner *RedisLimiter
	rdb   *redis.Client
}

func NewFailOpenLimiter(rdb *redis.Client) *FailOpenLimiter {
	return &FailOpenLimiter{
		inner: NewRedisLimiter(rdb),
		rdb:   rdb,
	}
}

// Allow returns (allowed, failOpen).
// failOpen=true means Redis was down — the request is allowed but unrated.
func (f *FailOpenLimiter) Allow(ctx context.Context, tenantID string, cfg BucketConfig) (allowed, failOpen bool) {
	if !f.redisHealthy(ctx) {
		slog.Warn("rate limiter: Redis unavailable, failing open", "tenant_id", tenantID)
		metrics.RateLimitFailOpenTotal.WithLabelValues(tenantID).Inc()
		return true, true
	}

	ok, err := f.inner.Allow(ctx, tenantID, cfg)
	if err != nil {
		slog.Warn("rate limiter: eval error, failing open", "tenant_id", tenantID, "error", err)
		metrics.RateLimitFailOpenTotal.WithLabelValues(tenantID).Inc()
		return true, true
	}

	if !ok {
		metrics.RateLimitExceededTotal.WithLabelValues(tenantID).Inc()
	}
	return ok, false
}

func (f *FailOpenLimiter) redisHealthy(ctx context.Context) bool {
	err := f.rdb.Ping(ctx).Err()
	return err == nil
}
