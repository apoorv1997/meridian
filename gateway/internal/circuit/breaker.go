package circuit

import (
	"context"
	"fmt"
	"log/slog"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/apoorv1997/meridian/gateway/internal/metrics"
)

type State int

const (
	StateClosed   State = 0 // normal — requests pass through
	StateOpen     State = 1 // rejecting — error rate exceeded threshold
	StateHalfOpen State = 2 // testing — one request allowed through
)

func (s State) String() string {
	switch s {
	case StateClosed:
		return "closed"
	case StateOpen:
		return "open"
	case StateHalfOpen:
		return "half_open"
	default:
		return "unknown"
	}
}

type Breaker struct {
	rdb                  *redis.Client
	errorThreshold       float64 // e.g. 0.05
	windowSeconds        int
	halfOpenDelaySeconds int
}

func New(rdb *redis.Client, errorThreshold float64, windowSeconds, halfOpenDelay int) *Breaker {
	return &Breaker{
		rdb:                  rdb,
		errorThreshold:       errorThreshold,
		windowSeconds:        windowSeconds,
		halfOpenDelaySeconds: halfOpenDelay,
	}
}

// IsOpen returns true when the circuit is open (provider should be skipped).
func (b *Breaker) IsOpen(ctx context.Context, provider string) bool {
	state := b.getState(ctx, provider)
	return state == StateOpen
}

// Allow returns whether a request should be forwarded to the provider.
// Transitions half-open → open or half-open → closed based on result.
func (b *Breaker) Allow(ctx context.Context, provider string) bool {
	state := b.getState(ctx, provider)
	switch state {
	case StateClosed:
		return true
	case StateOpen:
		// Check if half-open delay has elapsed
		openAt := b.getOpenAt(ctx, provider)
		if time.Since(openAt) > time.Duration(b.halfOpenDelaySeconds)*time.Second {
			b.setState(ctx, provider, StateHalfOpen)
			return true // allow one probe request
		}
		return false
	case StateHalfOpen:
		// Only one probe at a time — additional requests are blocked
		return false
	}
	return true
}

// RecordSuccess records a successful provider call and may close the circuit.
func (b *Breaker) RecordSuccess(ctx context.Context, provider string) {
	state := b.getState(ctx, provider)
	if state == StateHalfOpen {
		b.setState(ctx, provider, StateClosed)
		b.resetCounters(ctx, provider)
		slog.Info("circuit breaker closed", "provider", provider)
	}
	b.incrementTotal(ctx, provider)
	b.updateMetrics(ctx, provider)
}

// RecordFailure records a failed provider call and may open the circuit.
func (b *Breaker) RecordFailure(ctx context.Context, provider string) {
	state := b.getState(ctx, provider)
	if state == StateHalfOpen {
		// Probe failed — re-open
		b.openCircuit(ctx, provider)
		return
	}

	b.incrementError(ctx, provider)
	b.incrementTotal(ctx, provider)

	errors, total := b.getCounters(ctx, provider)
	if total >= 10 && float64(errors)/float64(total) > b.errorThreshold {
		b.openCircuit(ctx, provider)
		slog.Warn("circuit breaker opened", "provider", provider,
			"error_rate", float64(errors)/float64(total))
	}
	b.updateMetrics(ctx, provider)
}

func (b *Breaker) openCircuit(ctx context.Context, provider string) {
	b.setState(ctx, provider, StateOpen)
	b.rdb.Set(ctx, b.openAtKey(provider), time.Now().Unix(), time.Duration(b.windowSeconds*3)*time.Second)
	metrics.CircuitBreakerState.WithLabelValues(provider).Set(float64(StateOpen))
}

func (b *Breaker) getState(ctx context.Context, provider string) State {
	val, err := b.rdb.Get(ctx, b.stateKey(provider)).Result()
	if err != nil {
		return StateClosed // default to closed on Redis error
	}
	n, _ := strconv.Atoi(val)
	return State(n)
}

func (b *Breaker) setState(ctx context.Context, provider string, state State) {
	ttl := time.Duration(b.windowSeconds*10) * time.Second
	b.rdb.Set(ctx, b.stateKey(provider), int(state), ttl)
	metrics.CircuitBreakerState.WithLabelValues(provider).Set(float64(state))
}

func (b *Breaker) getOpenAt(ctx context.Context, provider string) time.Time {
	val, err := b.rdb.Get(ctx, b.openAtKey(provider)).Int64()
	if err != nil {
		return time.Time{}
	}
	return time.Unix(val, 0)
}

func (b *Breaker) incrementError(ctx context.Context, provider string) {
	key := b.errorKey(provider)
	b.rdb.Incr(ctx, key)
	b.rdb.Expire(ctx, key, time.Duration(b.windowSeconds)*time.Second)
}

func (b *Breaker) incrementTotal(ctx context.Context, provider string) {
	key := b.totalKey(provider)
	b.rdb.Incr(ctx, key)
	b.rdb.Expire(ctx, key, time.Duration(b.windowSeconds)*time.Second)
}

func (b *Breaker) getCounters(ctx context.Context, provider string) (errors, total int64) {
	errVal, _ := b.rdb.Get(ctx, b.errorKey(provider)).Int64()
	totVal, _ := b.rdb.Get(ctx, b.totalKey(provider)).Int64()
	return errVal, totVal
}

func (b *Breaker) resetCounters(ctx context.Context, provider string) {
	b.rdb.Del(ctx, b.errorKey(provider), b.totalKey(provider))
}

func (b *Breaker) updateMetrics(ctx context.Context, provider string) {
	state := b.getState(ctx, provider)
	metrics.CircuitBreakerState.WithLabelValues(provider).Set(float64(state))
}

func (b *Breaker) stateKey(provider string) string {
	return fmt.Sprintf("circuit:%s:state", provider)
}
func (b *Breaker) openAtKey(provider string) string {
	return fmt.Sprintf("circuit:%s:open_at", provider)
}
func (b *Breaker) errorKey(provider string) string {
	return fmt.Sprintf("circuit:%s:errors", provider)
}
func (b *Breaker) totalKey(provider string) string {
	return fmt.Sprintf("circuit:%s:total", provider)
}
