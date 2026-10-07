package proxy

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/apoorv1997/meridian/gateway/internal/metrics"
	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

// StreamProxy forwards the request to the provider and pipes the SSE stream
// directly to the client. Cost is estimated and reconciled asynchronously.
type StreamProxy struct {
	client    *http.Client
	providers map[string]*ProviderEndpoint
	tracer    TraceEmitter
}

func NewStreamProxy(providers map[string]*ProviderEndpoint, tracer TraceEmitter) *StreamProxy {
	return &StreamProxy{
		client:    &http.Client{Timeout: 5 * time.Minute},
		providers: providers,
		tracer:    tracer,
	}
}

// Forward streams the LLM response to c.Writer as Server-Sent Events.
func (s *StreamProxy) Forward(c *gin.Context, req *models.ChatRequest, provider, routeID, tenantID string) {
	span := &models.TraceSpan{
		TenantID:  tenantID,
		RouteID:   routeID,
		Provider:  provider,
		Model:     req.Model,
		StartedAt: time.Now(),
	}

	ep, ok := s.providers[provider]
	if !ok {
		c.JSON(http.StatusInternalServerError, gin.H{"error": fmt.Sprintf("no endpoint for provider %q", provider)})
		return
	}

	providerReq, err := ep.BuildRequest(c.Request.Context(), req)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "failed to build provider request"})
		return
	}

	metrics.ActiveRequests.WithLabelValues(routeID).Inc()
	defer metrics.ActiveRequests.WithLabelValues(routeID).Dec()

	start := time.Now()
	resp, err := s.client.Do(providerReq)
	if err != nil {
		metrics.ProviderErrorsTotal.WithLabelValues(provider, "network").Inc()
		c.JSON(http.StatusBadGateway, gin.H{"error": "provider unreachable"})
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		metrics.ProviderErrorsTotal.WithLabelValues(provider, fmt.Sprintf("http_%d", resp.StatusCode)).Inc()
		body, _ := io.ReadAll(resp.Body)
		c.Data(resp.StatusCode, "application/json", body)
		return
	}

	// Set SSE headers before writing any body bytes.
	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no")
	c.Status(http.StatusOK)

	flusher, canFlush := c.Writer.(http.Flusher)

	scanner := bufio.NewScanner(resp.Body)
	var outputTokens int
	for scanner.Scan() {
		line := scanner.Bytes()
		if _, err := c.Writer.Write(append(line, '\n')); err != nil {
			break
		}
		if canFlush {
			flusher.Flush()
		}
		// Rough token count from SSE data lines
		if bytes.HasPrefix(line, []byte("data: ")) && !bytes.Equal(line, []byte("data: [DONE]")) {
			outputTokens++
		}
	}

	latency := time.Since(start).Seconds()
	metrics.RequestDuration.WithLabelValues(routeID, provider, "stream").Observe(latency)
	metrics.ProviderRequestsTotal.WithLabelValues(provider, req.Model).Inc()
	metrics.TokensTotal.WithLabelValues(provider, req.Model, "output").Add(float64(outputTokens))

	span.LatencyMS = latency * 1000
	span.OutputTokens = outputTokens
	span.FinishedAt = time.Now()

	go func() {
		if err := s.tracer.Emit(context.Background(), span); err != nil {
			slog.Debug("trace emit failed", "error", err)
		}
	}()
}
