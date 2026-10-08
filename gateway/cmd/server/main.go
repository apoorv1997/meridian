package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"

	"github.com/apoorv1997/meridian/gateway/internal/auth"
	"github.com/apoorv1997/meridian/gateway/internal/cache"
	"github.com/apoorv1997/meridian/gateway/internal/circuit"
	"github.com/apoorv1997/meridian/gateway/internal/config"
	"github.com/apoorv1997/meridian/gateway/internal/kafka"
	"github.com/apoorv1997/meridian/gateway/internal/middleware"
	"github.com/apoorv1997/meridian/gateway/internal/proxy"
	"github.com/apoorv1997/meridian/gateway/internal/rag"
	"github.com/apoorv1997/meridian/gateway/internal/ratelimit"
	"github.com/apoorv1997/meridian/gateway/internal/router"
	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	})))

	cfg, err := config.Load()
	if err != nil {
		slog.Error("failed to load config", "error", err)
		os.Exit(1)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	// ── PostgreSQL ──────────────────────────────────────────────────────────
	poolCfg, err := pgxpool.ParseConfig(cfg.Database.URL)
	if err != nil {
		slog.Error("invalid DATABASE_URL", "error", err)
		os.Exit(1)
	}
	poolCfg.MaxConns = cfg.Database.MaxConns
	poolCfg.MinConns = cfg.Database.MinConns
	poolCfg.MaxConnIdleTime = time.Duration(cfg.Database.MaxIdleTime) * time.Second

	db, err := pgxpool.NewWithConfig(ctx, poolCfg)
	if err != nil {
		slog.Error("failed to connect to postgres", "error", err)
		os.Exit(1)
	}
	defer db.Close()

	if err := db.Ping(ctx); err != nil {
		slog.Error("postgres ping failed", "error", err)
		os.Exit(1)
	}
	slog.Info("postgres connected")

	// ── Redis ───────────────────────────────────────────────────────────────
	redisOpts, err := redis.ParseURL(cfg.Redis.URL)
	if err != nil {
		slog.Error("invalid REDIS_URL", "error", err)
		os.Exit(1)
	}
	rdb := redis.NewClient(redisOpts)
	defer rdb.Close()

	if err := rdb.Ping(ctx).Err(); err != nil {
		slog.Error("redis ping failed", "error", err)
		os.Exit(1)
	}
	slog.Info("redis connected")

	// ── Component wiring ────────────────────────────────────────────────────
	authenticator := auth.NewAuthenticator(db)
	adminHandler := auth.NewAdminHandler(db, cfg.Gateway.JWTSecret)
	rateLimiter := ratelimit.NewFailOpenLimiter(rdb)

	breaker := circuit.New(rdb,
		cfg.Gateway.CircuitErrorThreshold,
		cfg.Gateway.CircuitWindowSeconds,
		cfg.Gateway.CircuitHalfOpenDelay,
	)

	weightedRouter := router.NewWeightedRouter(rdb)
	failoverRouter := router.NewFailoverRouter(weightedRouter, breaker)
	rulesLoader := router.NewRulesLoader(db, &cfg.Providers)

	providerEndpoints := buildProviderEndpoints(cfg)

	var tracer proxy.TraceEmitter = proxy.NoOpTracer{}
	var kafkaTracer *kafka.Tracer
	if cfg.Gateway.KafkaBrokers != "" {
		brokers := splitBrokers(cfg.Gateway.KafkaBrokers)
		kafkaTracer = kafka.NewTracer(brokers, cfg.Gateway.KafkaTraceTopic)
		tracer = kafkaTracer
		slog.Info("kafka tracer configured",
			"brokers", cfg.Gateway.KafkaBrokers,
			"topic", cfg.Gateway.KafkaTraceTopic,
		)
	} else {
		slog.Warn("KAFKA_BROKERS not set — trace emission disabled")
	}
	bufferProxy := proxy.NewBufferProxy(providerEndpoints, tracer)
	streamProxy := proxy.NewStreamProxy(providerEndpoints, tracer)

	// RAG service: provides query-time retrieval AND the embedder backing the
	// semantic cache. Without it, both are disabled (embedder errors = cache miss).
	var embedder cache.Embedder = &noOpEmbedder{}
	var ragClient *rag.Client
	if cfg.Gateway.RAGServiceURL != "" {
		ragClient = rag.NewClient(cfg.Gateway.RAGServiceURL)
		embedder = ragClient
		slog.Info("rag service configured", "url", cfg.Gateway.RAGServiceURL)
	} else {
		slog.Warn("RAG_SERVICE_URL not set — RAG retrieval and semantic cache disabled")
	}

	semanticCache := cache.NewSemanticCache(db, embedder,
		cfg.Gateway.CacheSimilarityThreshold,
		cfg.Gateway.CacheTTLSeconds,
	)

	// ── Gin ─────────────────────────────────────────────────────────────────
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(middleware.Recovery())
	r.Use(middleware.RequestLogger())

	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok", "service": "meridian-gateway"})
	})
	r.GET("/metrics", gin.WrapH(promhttp.Handler()))
	r.POST("/admin/token", adminHandler.IssueAdminToken)

	// Authenticated routes
	authed := r.Group("/")
	authed.Use(middleware.APIKeyAuth(authenticator))
	authed.Use(rateLimitMiddleware(rateLimiter))
	authed.POST("/v1/chat/completions", chatHandler(rulesLoader, failoverRouter, bufferProxy, streamProxy, semanticCache, ragClient))
	authed.POST("/v1/cache/invalidate", cacheInvalidateHandler(db))

	// Admin routes (JWT-protected)
	admin := r.Group("/admin")
	admin.Use(adminHandler.JWTMiddleware())
	admin.POST("/keys", adminHandler.CreateKey)
	admin.DELETE("/keys/:id", adminHandler.RevokeKey)
	admin.GET("/keys", adminHandler.ListKeys)

	// ── HTTP server ─────────────────────────────────────────────────────────
	srv := &http.Server{
		Addr:         fmt.Sprintf(":%d", cfg.Gateway.Port),
		Handler:      r,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 5 * time.Minute,
		IdleTimeout:  120 * time.Second,
	}

	go func() {
		slog.Info("gateway listening", "port", cfg.Gateway.Port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("server error", "error", err)
			os.Exit(1)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	slog.Info("shutting down gateway...")
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer shutdownCancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		slog.Error("shutdown error", "error", err)
	}
	if kafkaTracer != nil {
		if err := kafkaTracer.Close(); err != nil {
			slog.Warn("kafka tracer close error", "error", err)
		}
	}
	slog.Info("gateway stopped")
}

func splitBrokers(s string) []string {
	var out []string
	for _, b := range strings.Split(s, ",") {
		if b = strings.TrimSpace(b); b != "" {
			out = append(out, b)
		}
	}
	return out
}

func chatHandler(
	rules *router.RulesLoader,
	failover *router.FailoverRouter,
	bufProxy *proxy.BufferProxy,
	streamProxy *proxy.StreamProxy,
	semCache *cache.SemanticCache,
	ragClient *rag.Client,
) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req models.ChatRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}

		tenantID := middleware.TenantIDFromGin(c)
		routeID := req.RouteID
		if routeID == "" {
			routeID = "default"
		}

		rule, err := rules.Load(c.Request.Context(), tenantID, routeID)
		if err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"error": "no route available"})
			return
		}

		mode := rule.ProxyMode
		if req.Stream {
			mode = "stream"
		}

		// Semantic cache — buffer mode only (ADR-004). Checked BEFORE RAG
		// retrieval: cache entries are tagged with document IDs and evicted
		// on document updates, so a hit is known-fresh.
		if mode == "buffer" {
			cached, _ := semCache.Get(c.Request.Context(), &req, tenantID, routeID)
			if cached != nil {
				c.Data(http.StatusOK, "application/json", cached)
				return
			}
		}

		// RAG retrieval — inject context ahead of the user's messages.
		var ragDocIDs []string
		if rule.RAGEnabled && ragClient != nil {
			result, err := ragClient.Retrieve(c.Request.Context(), lastUserMessage(req.Messages), tenantID)
			if err != nil {
				// Degrade gracefully: answer without context rather than fail.
				slog.Warn("rag retrieval failed, continuing without context", "error", err)
			} else if result.Context != "" {
				injectContext(&req, result.Context)
				ragDocIDs = result.DocumentIDs
			}
		}

		provider, err := failover.Pick(c.Request.Context(), tenantID, routeID, rule.Providers)
		if err != nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"error": err.Error()})
			return
		}

		switch mode {
		case "stream":
			req.Stream = true
			streamProxy.Forward(c, &req, provider, routeID, tenantID)
		default:
			req.Stream = false
			_, chatResp, proxyErr := bufProxy.Forward(c, &req, provider, routeID, tenantID)
			// Cache the translated (OpenAI-compatible) response, not the raw
			// provider body — cache hits must return the same format as misses.
			// ragDocIDs tag the entry for invalidation when documents change.
			if proxyErr == nil && chatResp != nil {
				translated, err := json.Marshal(chatResp)
				if err == nil {
					go semCache.Set(context.Background(), &req, tenantID, translated, ragDocIDs) //nolint:errcheck
				}
			}
		}
	}
}

// lastUserMessage returns the most recent user-role message content —
// the retrieval query for RAG.
func lastUserMessage(messages []models.ChatMessage) string {
	for i := len(messages) - 1; i >= 0; i-- {
		if messages[i].Role == "user" {
			return messages[i].Content
		}
	}
	return ""
}

// injectContext prepends retrieved document context to the request's system
// prompt so it applies to the whole conversation without altering user turns.
func injectContext(req *models.ChatRequest, ctx string) {
	block := "Use the following retrieved context to answer the user's question. " +
		"If the context is not relevant, say so rather than inventing an answer.\n\n" +
		"<retrieved_context>\n" + ctx + "\n</retrieved_context>"
	if req.System != "" {
		req.System = req.System + "\n\n" + block
	} else {
		req.System = block
	}
}

func rateLimitMiddleware(limiter *ratelimit.FailOpenLimiter) gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID := middleware.TenantIDFromGin(c)
		allowed, _ := limiter.Allow(c.Request.Context(), tenantID, ratelimit.DefaultBucketConfig())
		if !allowed {
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{"error": "rate limit exceeded"})
			return
		}
		c.Next()
	}
}

func cacheInvalidateHandler(db *pgxpool.Pool) gin.HandlerFunc {
	inv := cache.NewInvalidator(db)
	return func(c *gin.Context) {
		var req struct {
			DocumentID string `json:"document_id" binding:"required"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}
		tenantID := middleware.TenantIDFromGin(c)
		n, err := inv.ByDocumentID(c.Request.Context(), tenantID, req.DocumentID)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "invalidation failed"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"invalidated": n})
	}
}

func buildProviderEndpoints(cfg *config.Config) map[string]*proxy.ProviderEndpoint {
	eps := make(map[string]*proxy.ProviderEndpoint)
	if cfg.Providers.Anthropic.Enabled {
		eps[models.ProviderAnthropic] = &proxy.ProviderEndpoint{
			Name:    models.ProviderAnthropic,
			BaseURL: cfg.Providers.Anthropic.BaseURL,
			APIKey:  cfg.Providers.Anthropic.APIKey,
		}
	}
	if cfg.Providers.OpenAI.Enabled {
		eps[models.ProviderOpenAI] = &proxy.ProviderEndpoint{
			Name:    models.ProviderOpenAI,
			BaseURL: cfg.Providers.OpenAI.BaseURL,
			APIKey:  cfg.Providers.OpenAI.APIKey,
		}
	}
	if cfg.Providers.Google.Enabled {
		eps[models.ProviderGoogle] = &proxy.ProviderEndpoint{
			Name:    models.ProviderGoogle,
			BaseURL: cfg.Providers.Google.BaseURL,
			APIKey:  cfg.Providers.Google.APIKey,
		}
	}
	return eps
}

// noOpEmbedder disables the semantic cache by erroring on every Embed call.
// Zero vectors are NOT safe here: cosine distance against a zero vector is
// NaN, which Postgres orders above every number — any prompt in the namespace
// would match any cached entry. Replace with a real embedder (sentence-
// transformers sidecar) in Phase 2 when the RAG engine is live.
type noOpEmbedder struct{}

func (noOpEmbedder) Embed(_ context.Context, _ string) ([]float32, error) {
	return nil, fmt.Errorf("embedder not configured — semantic cache disabled until Phase 2")
}
