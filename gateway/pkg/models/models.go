package models

import "time"

// Provider names
const (
	ProviderAnthropic = "anthropic"
	ProviderOpenAI    = "openai"
	ProviderGoogle    = "google"
)

// Context keys — typed to avoid collisions with other packages
type ContextKey string

const (
	ContextKeyTenantID ContextKey = "tenant_id"
	ContextKeyAPIKeyID ContextKey = "api_key_id"
	ContextKeyRouteID  ContextKey = "route_id"
	ContextKeyTraceID  ContextKey = "trace_id"
)

// ── Chat API types (OpenAI-compatible) ───────────────────────────────────────

type ChatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ChatRequest struct {
	Model       string        `json:"model" binding:"required"`
	Messages    []ChatMessage `json:"messages" binding:"required,min=1"`
	Stream      bool          `json:"stream"`
	MaxTokens   int           `json:"max_tokens,omitempty"`
	Temperature float64       `json:"temperature,omitempty"`
	System      string        `json:"system,omitempty"`
	RouteID     string        `json:"route_id,omitempty"`
}

type ChatChoice struct {
	Index        int         `json:"index"`
	Message      ChatMessage `json:"message"`
	FinishReason string      `json:"finish_reason"`
}

type UsageStats struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

type ChatResponse struct {
	ID      string       `json:"id"`
	Object  string       `json:"object"`
	Created int64        `json:"created"`
	Model   string       `json:"model"`
	Choices []ChatChoice `json:"choices"`
	Usage   UsageStats   `json:"usage"`
}

// ── Domain models ─────────────────────────────────────────────────────────────

type Tenant struct {
	ID           string    `db:"id"`
	Name         string    `db:"name"`
	RateLimitRPM int       `db:"rate_limit_rpm"`
	RateLimitTPM int       `db:"rate_limit_tpm"`
	CreatedAt    time.Time `db:"created_at"`
}

type APIKey struct {
	ID        string     `db:"id"`
	TenantID  string     `db:"tenant_id"`
	KeyHash   string     `db:"key_hash"`
	Name      string     `db:"name"`
	RevokedAt *time.Time `db:"revoked_at"`
	ExpiresAt *time.Time `db:"expires_at"`
	CreatedAt time.Time  `db:"created_at"`
}

type RouteConfig struct {
	ID             string    `db:"id"`
	TenantID       string    `db:"tenant_id"`
	RouteID        string    `db:"route_id"`
	ProxyMode      string    `db:"proxy_mode"` // "buffer" | "stream"
	RAGEnabled     bool      `db:"rag_enabled"`
	EvalRubrics    []string  `db:"eval_rubrics"`
	DriftThreshold float64   `db:"drift_threshold"`
	CreatedAt      time.Time `db:"created_at"`
}

type ProviderWeight struct {
	TenantID string  `db:"tenant_id"`
	RouteID  string  `db:"route_id"`
	Provider string  `db:"provider"`
	Weight   float64 `db:"weight"`
}

// ── OTel span for Kafka emission ──────────────────────────────────────────────

type TraceSpan struct {
	TraceID      string            `json:"trace_id"`
	SpanID       string            `json:"span_id"`
	TenantID     string            `json:"tenant_id"`
	RouteID      string            `json:"route_id"`
	Provider     string            `json:"provider"`
	Model        string            `json:"model"`
	LatencyMS    float64           `json:"latency_ms"`
	InputTokens  int               `json:"input_tokens"`
	OutputTokens int               `json:"output_tokens"`
	CostUSD      float64           `json:"cost_usd"`
	Cached       bool              `json:"cached"`
	Error        string            `json:"error,omitempty"`
	Attributes   map[string]string `json:"attributes,omitempty"`
	StartedAt    time.Time         `json:"started_at"`
	FinishedAt   time.Time         `json:"finished_at"`
}
