package proxy

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/apoorv1997/meridian/gateway/internal/metrics"
	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

// BufferProxy accumulates the full provider response before returning it.
// This mode enables semantic caching and exact cost computation.
type BufferProxy struct {
	client    *http.Client
	providers map[string]*ProviderEndpoint
	tracer    TraceEmitter
}

func NewBufferProxy(providers map[string]*ProviderEndpoint, tracer TraceEmitter) *BufferProxy {
	return &BufferProxy{
		client:    &http.Client{Timeout: 2 * time.Minute},
		providers: providers,
		tracer:    tracer,
	}
}

// Forward sends the request to the provider, buffers the full response,
// and returns it to the client. Returns the response body for caching.
func (b *BufferProxy) Forward(c *gin.Context, req *models.ChatRequest, provider, routeID, tenantID string) ([]byte, *models.ChatResponse, error) {
	ep, ok := b.providers[provider]
	if !ok {
		return nil, nil, fmt.Errorf("proxy: no endpoint for provider %q", provider)
	}

	providerReq, err := ep.BuildRequest(c.Request.Context(), req)
	if err != nil {
		return nil, nil, fmt.Errorf("proxy: build request: %w", err)
	}

	metrics.ActiveRequests.WithLabelValues(routeID).Inc()
	defer metrics.ActiveRequests.WithLabelValues(routeID).Dec()

	start := time.Now()
	resp, err := b.client.Do(providerReq)
	if err != nil {
		metrics.ProviderErrorsTotal.WithLabelValues(provider, "network").Inc()
		return nil, nil, fmt.Errorf("proxy: provider unreachable: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 10<<20)) // 10 MB cap
	if err != nil {
		return nil, nil, fmt.Errorf("proxy: read body: %w", err)
	}

	latency := time.Since(start).Seconds()
	metrics.ProviderLatency.WithLabelValues(provider, req.Model).Observe(latency)

	if resp.StatusCode >= 400 {
		metrics.ProviderErrorsTotal.WithLabelValues(provider, fmt.Sprintf("http_%d", resp.StatusCode)).Inc()
		c.Data(resp.StatusCode, "application/json", body)
		return nil, nil, fmt.Errorf("proxy: provider returned %d", resp.StatusCode)
	}

	chatResp, err := parseProviderResponse(provider, body)
	if err != nil {
		// Unknown response shape — pass through as-is.
		c.Data(http.StatusOK, resp.Header.Get("Content-Type"), body)
		return body, nil, nil
	}

	metrics.RequestDuration.WithLabelValues(routeID, provider, "buffer").Observe(latency)
	metrics.ProviderRequestsTotal.WithLabelValues(provider, req.Model).Inc()
	metrics.TokensTotal.WithLabelValues(provider, req.Model, "input").Add(float64(chatResp.Usage.PromptTokens))
	metrics.TokensTotal.WithLabelValues(provider, req.Model, "output").Add(float64(chatResp.Usage.CompletionTokens))

	costUSD := estimateCost(provider, req.Model, chatResp.Usage)
	metrics.CostUSDTotal.WithLabelValues(provider, req.Model, tenantID).Add(costUSD)

	span := &models.TraceSpan{
		TenantID:     tenantID,
		RouteID:      routeID,
		Provider:     provider,
		Model:        req.Model,
		LatencyMS:    latency * 1000,
		InputTokens:  chatResp.Usage.PromptTokens,
		OutputTokens: chatResp.Usage.CompletionTokens,
		CostUSD:      costUSD,
		StartedAt:    start,
		FinishedAt:   time.Now(),
	}
	go func() {
		if err := b.tracer.Emit(context.Background(), span); err != nil {
			slog.Debug("trace emit failed", "error", err)
		}
	}()

	c.JSON(http.StatusOK, chatResp)
	return body, chatResp, nil
}

// parseProviderResponse normalizes provider-specific response formats into
// the OpenAI-compatible ChatResponse shape.
func parseProviderResponse(provider string, body []byte) (*models.ChatResponse, error) {
	switch provider {
	case models.ProviderAnthropic:
		return parseAnthropicResponse(body)
	default:
		var chatResp models.ChatResponse
		if err := json.Unmarshal(body, &chatResp); err != nil {
			return nil, err
		}
		return &chatResp, nil
	}
}

// anthropicResponse mirrors the Anthropic Messages API response shape.
type anthropicResponse struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	Role    string `json:"role"`
	Model   string `json:"model"`
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	StopReason string `json:"stop_reason"`
	Usage      struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage"`
}

func parseAnthropicResponse(body []byte) (*models.ChatResponse, error) {
	var ar anthropicResponse
	if err := json.Unmarshal(body, &ar); err != nil {
		return nil, err
	}
	if ar.Type != "message" {
		return nil, fmt.Errorf("proxy: unexpected anthropic response type %q", ar.Type)
	}

	var text string
	for _, block := range ar.Content {
		if block.Type == "text" {
			text += block.Text
		}
	}

	finishReason := "stop"
	switch ar.StopReason {
	case "max_tokens":
		finishReason = "length"
	case "tool_use":
		finishReason = "tool_calls"
	case "refusal":
		finishReason = "content_filter"
	}

	return &models.ChatResponse{
		ID:      ar.ID,
		Object:  "chat.completion",
		Created: time.Now().Unix(),
		Model:   ar.Model,
		Choices: []models.ChatChoice{{
			Index:        0,
			Message:      models.ChatMessage{Role: "assistant", Content: text},
			FinishReason: finishReason,
		}},
		Usage: models.UsageStats{
			PromptTokens:     ar.Usage.InputTokens,
			CompletionTokens: ar.Usage.OutputTokens,
			TotalTokens:      ar.Usage.InputTokens + ar.Usage.OutputTokens,
		},
	}, nil
}

// estimateCost returns a rough USD cost based on public pricing.
func estimateCost(provider, model string, usage models.UsageStats) float64 {
	type pricing struct{ input, output float64 } // per 1M tokens
	prices := map[string]pricing{
		"claude-fable-5":    {10.0, 50.0},
		"claude-opus-4-8":   {5.0, 25.0},
		"claude-sonnet-4-6": {3.0, 15.0},
		"claude-haiku-4-5":  {1.0, 5.0},
		"gpt-4o":            {5.0, 15.0},
		"gpt-4o-mini":       {0.15, 0.60},
		"gemini-1.5-pro":    {3.5, 10.5},
		"gemini-1.5-flash":  {0.075, 0.30},
	}
	p, ok := prices[model]
	if !ok {
		p = pricing{5.0, 15.0} // fallback
	}
	return (float64(usage.PromptTokens)*p.input + float64(usage.CompletionTokens)*p.output) / 1_000_000
}

// ProviderEndpoint holds the base URL and auth for a single LLM provider.
type ProviderEndpoint struct {
	Name    string
	BaseURL string
	APIKey  string
}

// BuildRequest constructs an outgoing HTTP request for the provider.
// Uses OpenAI-compatible format; Anthropic gets translated separately.
func (ep *ProviderEndpoint) BuildRequest(ctx context.Context, req *models.ChatRequest) (*http.Request, error) {
	var url string
	switch ep.Name {
	case models.ProviderAnthropic:
		url = ep.BaseURL + "/v1/messages"
	case models.ProviderOpenAI:
		url = ep.BaseURL + "/v1/chat/completions"
	case models.ProviderGoogle:
		url = fmt.Sprintf("%s/v1beta/models/%s:generateContent?key=%s", ep.BaseURL, req.Model, ep.APIKey)
	default:
		url = ep.BaseURL + "/v1/chat/completions"
	}

	var body []byte
	var err error

	switch ep.Name {
	case models.ProviderAnthropic:
		body, err = buildAnthropicBody(req)
	default:
		body, err = buildOpenAIBody(req)
	}
	if err != nil {
		return nil, err
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/json")

	switch ep.Name {
	case models.ProviderAnthropic:
		httpReq.Header.Set("x-api-key", ep.APIKey)
		httpReq.Header.Set("anthropic-version", "2023-06-01")
	case models.ProviderOpenAI:
		httpReq.Header.Set("Authorization", "Bearer "+ep.APIKey)
	}

	return httpReq, nil
}

func buildOpenAIBody(req *models.ChatRequest) ([]byte, error) {
	payload := map[string]any{
		"model":    req.Model,
		"messages": req.Messages,
		"stream":   req.Stream,
	}
	if req.MaxTokens > 0 {
		payload["max_tokens"] = req.MaxTokens
	}
	if req.Temperature > 0 {
		payload["temperature"] = req.Temperature
	}
	return json.Marshal(payload)
}

func buildAnthropicBody(req *models.ChatRequest) ([]byte, error) {
	// Separate system message from user messages per Anthropic API spec.
	system := req.System
	var messages []models.ChatMessage
	for _, m := range req.Messages {
		if m.Role == "system" {
			system = m.Content
		} else {
			messages = append(messages, m)
		}
	}

	payload := map[string]any{
		"model":    req.Model,
		"messages": messages,
		"max_tokens": func() int {
			if req.MaxTokens > 0 {
				return req.MaxTokens
			}
			return 1024
		}(),
		"stream": req.Stream,
	}
	if system != "" {
		payload["system"] = system
	}
	if req.Temperature > 0 {
		payload["temperature"] = req.Temperature
	}
	return json.Marshal(payload)
}

// TraceEmitter abstracts OTel span emission (Kafka or no-op).
type TraceEmitter interface {
	Emit(ctx context.Context, span *models.TraceSpan) error
}

// NoOpTracer discards spans. Used when Kafka is not configured.
type NoOpTracer struct{}

func (NoOpTracer) Emit(_ context.Context, _ *models.TraceSpan) error { return nil }
