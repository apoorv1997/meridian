package proxy

import (
	"context"
	"encoding/json"
	"math"
	"testing"

	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

func TestParseAnthropicResponse(t *testing.T) {
	tests := []struct {
		name        string
		body        string
		wantErr     bool
		wantContent string
		wantFinish  string
		wantInput   int
		wantOutput  int
	}{
		{
			name: "normal response",
			body: `{
				"id": "msg_123", "type": "message", "role": "assistant",
				"model": "claude-haiku-4-5",
				"content": [{"type": "text", "text": "Hello!"}],
				"stop_reason": "end_turn",
				"usage": {"input_tokens": 13, "output_tokens": 12}
			}`,
			wantContent: "Hello!",
			wantFinish:  "stop",
			wantInput:   13,
			wantOutput:  12,
		},
		{
			name: "multiple text blocks concatenated",
			body: `{
				"id": "msg_1", "type": "message",
				"content": [{"type": "text", "text": "part1 "}, {"type": "text", "text": "part2"}],
				"stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 2}
			}`,
			wantContent: "part1 part2",
			wantFinish:  "stop",
			wantInput:   1,
			wantOutput:  2,
		},
		{
			name: "max_tokens maps to length",
			body: `{
				"id": "msg_1", "type": "message",
				"content": [{"type": "text", "text": "trunc"}],
				"stop_reason": "max_tokens", "usage": {"input_tokens": 1, "output_tokens": 2}
			}`,
			wantContent: "trunc",
			wantFinish:  "length",
			wantInput:   1,
			wantOutput:  2,
		},
		{
			name:    "error response rejected",
			body:    `{"type": "error", "error": {"type": "not_found_error", "message": "model not found"}}`,
			wantErr: true,
		},
		{
			name:    "invalid json rejected",
			body:    `not json`,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := parseAnthropicResponse([]byte(tt.body))
			if (err != nil) != tt.wantErr {
				t.Fatalf("parseAnthropicResponse() error = %v, wantErr %v", err, tt.wantErr)
			}
			if tt.wantErr {
				return
			}
			if len(got.Choices) != 1 {
				t.Fatalf("expected 1 choice, got %d", len(got.Choices))
			}
			if got.Choices[0].Message.Content != tt.wantContent {
				t.Errorf("content = %q, want %q", got.Choices[0].Message.Content, tt.wantContent)
			}
			if got.Choices[0].FinishReason != tt.wantFinish {
				t.Errorf("finish_reason = %q, want %q", got.Choices[0].FinishReason, tt.wantFinish)
			}
			if got.Usage.PromptTokens != tt.wantInput || got.Usage.CompletionTokens != tt.wantOutput {
				t.Errorf("usage = %+v, want input=%d output=%d", got.Usage, tt.wantInput, tt.wantOutput)
			}
			if got.Usage.TotalTokens != tt.wantInput+tt.wantOutput {
				t.Errorf("total_tokens = %d, want %d", got.Usage.TotalTokens, tt.wantInput+tt.wantOutput)
			}
		})
	}
}

func TestBuildAnthropicBody(t *testing.T) {
	t.Run("system message extracted from messages array", func(t *testing.T) {
		req := &models.ChatRequest{
			Model: "claude-haiku-4-5",
			Messages: []models.ChatMessage{
				{Role: "system", Content: "Be terse."},
				{Role: "user", Content: "Hi"},
			},
		}
		body, err := buildAnthropicBody(req)
		if err != nil {
			t.Fatal(err)
		}
		var payload map[string]any
		if err := json.Unmarshal(body, &payload); err != nil {
			t.Fatal(err)
		}
		if payload["system"] != "Be terse." {
			t.Errorf("system = %v, want %q", payload["system"], "Be terse.")
		}
		msgs := payload["messages"].([]any)
		if len(msgs) != 1 {
			t.Errorf("messages should exclude system role, got %d entries", len(msgs))
		}
	})

	t.Run("default max_tokens applied", func(t *testing.T) {
		req := &models.ChatRequest{
			Model:    "claude-haiku-4-5",
			Messages: []models.ChatMessage{{Role: "user", Content: "Hi"}},
		}
		body, _ := buildAnthropicBody(req)
		var payload map[string]any
		json.Unmarshal(body, &payload) //nolint:errcheck
		if payload["max_tokens"].(float64) <= 0 {
			t.Error("anthropic requires max_tokens > 0; default not applied")
		}
	})
}

func TestBuildRequestHeaders(t *testing.T) {
	req := &models.ChatRequest{
		Model:    "claude-haiku-4-5",
		Messages: []models.ChatMessage{{Role: "user", Content: "Hi"}},
	}

	t.Run("anthropic auth header", func(t *testing.T) {
		ep := &ProviderEndpoint{Name: models.ProviderAnthropic, BaseURL: "https://api.anthropic.com", APIKey: "test-key"}
		httpReq, err := ep.BuildRequest(context.Background(), req)
		if err != nil {
			t.Fatal(err)
		}
		if got := httpReq.Header.Get("x-api-key"); got != "test-key" {
			t.Errorf("x-api-key = %q, want %q", got, "test-key")
		}
		if httpReq.Header.Get("anthropic-version") == "" {
			t.Error("anthropic-version header missing")
		}
		if httpReq.URL.Path != "/v1/messages" {
			t.Errorf("path = %q, want /v1/messages", httpReq.URL.Path)
		}
	})

	t.Run("openai auth header", func(t *testing.T) {
		ep := &ProviderEndpoint{Name: models.ProviderOpenAI, BaseURL: "https://api.openai.com", APIKey: "sk-test"}
		httpReq, err := ep.BuildRequest(context.Background(), req)
		if err != nil {
			t.Fatal(err)
		}
		if got := httpReq.Header.Get("Authorization"); got != "Bearer sk-test" {
			t.Errorf("Authorization = %q, want %q", got, "Bearer sk-test")
		}
	})
}

func TestEstimateCost(t *testing.T) {
	usage := models.UsageStats{PromptTokens: 1_000_000, CompletionTokens: 1_000_000}

	tests := []struct {
		model string
		want  float64
	}{
		{"claude-haiku-4-5", 6.0},   // $1 in + $5 out
		{"claude-sonnet-4-6", 18.0}, // $3 in + $15 out
		{"claude-opus-4-8", 30.0},   // $5 in + $25 out
		{"unknown-model", 20.0},     // fallback $5 + $15
	}
	for _, tt := range tests {
		if got := estimateCost("anthropic", tt.model, usage); math.Abs(got-tt.want) > 1e-9 {
			t.Errorf("estimateCost(%q) = %v, want %v", tt.model, got, tt.want)
		}
	}
}
