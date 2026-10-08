package tests

import (
	"context"
	"testing"
	"time"

	goredis "github.com/redis/go-redis/v9"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"

	"github.com/apoorv1997/meridian/gateway/internal/circuit"
	"github.com/apoorv1997/meridian/gateway/internal/ratelimit"
	"github.com/apoorv1997/meridian/gateway/internal/router"
)

// startRedis spins up a throwaway Redis container and returns a connected client.
func startRedis(t *testing.T) *goredis.Client {
	t.Helper()
	ctx := context.Background()

	container, err := tcredis.Run(ctx, "redis:7.2-alpine")
	if err != nil {
		t.Fatalf("failed to start redis container: %v", err)
	}
	t.Cleanup(func() { container.Terminate(context.Background()) }) //nolint:errcheck

	uri, err := container.ConnectionString(ctx)
	if err != nil {
		t.Fatal(err)
	}
	opts, err := goredis.ParseURL(uri)
	if err != nil {
		t.Fatal(err)
	}
	rdb := goredis.NewClient(opts)
	t.Cleanup(func() { rdb.Close() })
	return rdb
}

func TestRateLimiter(t *testing.T) {
	if testing.Short() {
		t.Skip("requires docker")
	}
	rdb := startRedis(t)
	ctx := context.Background()
	limiter := ratelimit.NewRedisLimiter(rdb)

	t.Run("allows up to capacity then denies", func(t *testing.T) {
		cfg := ratelimit.BucketConfig{Capacity: 5, RefillRate: 0.001} // effectively no refill
		tenant := "tenant-capacity"

		for i := 0; i < 5; i++ {
			ok, err := limiter.Allow(ctx, tenant, cfg)
			if err != nil {
				t.Fatal(err)
			}
			if !ok {
				t.Fatalf("request %d denied; should be within capacity", i+1)
			}
		}

		ok, err := limiter.Allow(ctx, tenant, cfg)
		if err != nil {
			t.Fatal(err)
		}
		if ok {
			t.Error("request over capacity was allowed")
		}
	})

	t.Run("refills over time", func(t *testing.T) {
		cfg := ratelimit.BucketConfig{Capacity: 1, RefillRate: 10} // 10 tokens/sec
		tenant := "tenant-refill"

		if ok, _ := limiter.Allow(ctx, tenant, cfg); !ok {
			t.Fatal("first request should be allowed")
		}
		if ok, _ := limiter.Allow(ctx, tenant, cfg); ok {
			t.Fatal("bucket should be empty immediately after")
		}

		time.Sleep(200 * time.Millisecond) // refills 2 tokens, capped at capacity 1
		if ok, _ := limiter.Allow(ctx, tenant, cfg); !ok {
			t.Error("bucket should have refilled after 200ms at 10 tokens/sec")
		}
	})

	t.Run("tenants have isolated buckets", func(t *testing.T) {
		cfg := ratelimit.BucketConfig{Capacity: 1, RefillRate: 0.001}

		if ok, _ := limiter.Allow(ctx, "tenant-x", cfg); !ok {
			t.Fatal("tenant-x first request denied")
		}
		if ok, _ := limiter.Allow(ctx, "tenant-x", cfg); ok {
			t.Fatal("tenant-x should be exhausted")
		}
		// tenant-y must be unaffected by tenant-x's exhausted bucket
		if ok, _ := limiter.Allow(ctx, "tenant-y", cfg); !ok {
			t.Error("tenant-y was denied due to tenant-x's bucket")
		}
	})
}

// ADR-007: on Redis failure the limiter must allow the request, never reject.
func TestFailOpenWhenRedisDown(t *testing.T) {
	if testing.Short() {
		t.Skip("requires docker")
	}
	// Point at a port where nothing is listening.
	rdb := goredis.NewClient(&goredis.Options{Addr: "localhost:1", DialTimeout: 100 * time.Millisecond})
	defer rdb.Close()

	limiter := ratelimit.NewFailOpenLimiter(rdb)
	allowed, failOpen := limiter.Allow(context.Background(), "tenant-a", ratelimit.DefaultBucketConfig())

	if !allowed {
		t.Error("ADR-007 violation: request rejected while Redis is down")
	}
	if !failOpen {
		t.Error("failOpen flag should be set when Redis is unreachable")
	}
}

func TestCircuitBreaker(t *testing.T) {
	if testing.Short() {
		t.Skip("requires docker")
	}
	rdb := startRedis(t)
	ctx := context.Background()

	t.Run("opens after error rate exceeds threshold", func(t *testing.T) {
		// 5% threshold, 60s window, 1s half-open delay
		breaker := circuit.New(rdb, 0.05, 60, 1)
		provider := "provider-opens"

		// 10 failures out of 10 = 100% error rate, above 5%
		for i := 0; i < 10; i++ {
			breaker.RecordFailure(ctx, provider)
		}

		if breaker.Allow(ctx, provider) {
			t.Error("circuit should be open after 100% error rate over 10 requests")
		}
	})

	t.Run("stays closed below minimum request volume", func(t *testing.T) {
		breaker := circuit.New(rdb, 0.05, 60, 1)
		provider := "provider-low-volume"

		// Fewer than 10 requests must never trip the breaker, even at 100% errors.
		for i := 0; i < 5; i++ {
			breaker.RecordFailure(ctx, provider)
		}
		if !breaker.Allow(ctx, provider) {
			t.Error("circuit opened below the 10-request minimum volume")
		}
	})

	t.Run("half-open probe then close on success", func(t *testing.T) {
		breaker := circuit.New(rdb, 0.05, 60, 0) // 0s half-open delay for test speed
		provider := "provider-recovers"

		for i := 0; i < 10; i++ {
			breaker.RecordFailure(ctx, provider)
		}
		if !breaker.IsOpen(ctx, provider) {
			t.Fatal("circuit should be open")
		}

		// Delay elapsed (0s) — next Allow transitions to half-open and permits a probe.
		time.Sleep(50 * time.Millisecond)
		if !breaker.Allow(ctx, provider) {
			t.Fatal("probe request should be allowed after half-open delay")
		}

		// Successful probe closes the circuit.
		breaker.RecordSuccess(ctx, provider)
		if !breaker.Allow(ctx, provider) {
			t.Error("circuit should be closed after successful probe")
		}
	})
}

func TestRoutingWeightsAreIsolatedPerTenant(t *testing.T) {
	if testing.Short() {
		t.Skip("requires docker")
	}
	rdb := startRedis(t)
	ctx := context.Background()
	wr := router.NewWeightedRouter(rdb)
	providers := []string{"a", "b"}

	// Tenant A's evaluation loop has pushed provider "b" down to the floor on route "default".
	if err := wr.SetWeight(ctx, "tenant-a", "default", "a", 0.95); err != nil {
		t.Fatal(err)
	}
	if err := wr.SetWeight(ctx, "tenant-a", "default", "b", 0.05); err != nil {
		t.Fatal(err)
	}

	share := func(tenant string) float64 {
		hits := 0
		for i := 0; i < 2000; i++ {
			p, err := wr.Select(ctx, tenant, "default", providers)
			if err != nil {
				t.Fatal(err)
			}
			if p == "b" {
				hits++
			}
		}
		return float64(hits) / 2000
	}

	if got := share("tenant-a"); got > 0.10 {
		t.Errorf("tenant-a picked b %.2f of the time, want about 0.05", got)
	}
	// Tenant B has no stored weights for its own "default" route, so it splits evenly.
	if got := share("tenant-b"); got < 0.40 || got > 0.60 {
		t.Errorf("tenant-b picked b %.2f of the time, want about 0.5: tenant-a's weights leaked", got)
	}
}
