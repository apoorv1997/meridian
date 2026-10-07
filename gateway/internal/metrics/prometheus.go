package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	RequestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "requests_total",
		Help:      "Total number of requests received.",
	}, []string{"route_id", "provider", "status_code"})

	RequestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "request_duration_seconds",
		Help:      "End-to-end request latency.",
		Buckets:   []float64{.05, .1, .25, .5, 1, 2.5, 5, 10, 30},
	}, []string{"route_id", "provider", "proxy_mode"})

	ProviderRequestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "provider_requests_total",
		Help:      "Requests forwarded to each provider.",
	}, []string{"provider", "model"})

	ProviderErrorsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "provider_errors_total",
		Help:      "Errors returned by providers.",
	}, []string{"provider", "error_type"})

	ProviderLatency = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "provider_latency_seconds",
		Help:      "Latency of provider API calls.",
		Buckets:   []float64{.1, .25, .5, 1, 2.5, 5, 10, 30, 60},
	}, []string{"provider", "model"})

	CacheHitsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "cache_hits_total",
		Help:      "Semantic cache hits.",
	}, []string{"route_id"})

	CacheMissesTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "cache_misses_total",
		Help:      "Semantic cache misses.",
	}, []string{"route_id"})

	RateLimitExceededTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "rate_limit_exceeded_total",
		Help:      "Number of requests rejected by rate limiter.",
	}, []string{"tenant_id"})

	RateLimitFailOpenTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "rate_limit_fail_open_total",
		Help:      "Requests allowed through due to Redis unavailability.",
	}, []string{"tenant_id"})

	CircuitBreakerState = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "circuit_breaker_state",
		Help:      "Circuit breaker state: 0=closed, 1=open, 2=half_open.",
	}, []string{"provider"})

	TokensTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "tokens_total",
		Help:      "Total tokens processed.",
	}, []string{"provider", "model", "type"}) // type: input|output

	CostUSDTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "cost_usd_total",
		Help:      "Estimated LLM cost in USD.",
	}, []string{"provider", "model", "tenant_id"})

	ActiveRequests = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Namespace: "meridian",
		Subsystem: "gateway",
		Name:      "active_requests",
		Help:      "Number of requests currently in flight.",
	}, []string{"route_id"})
)
