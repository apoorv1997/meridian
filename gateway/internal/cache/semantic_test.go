package cache

import (
	"strings"
	"testing"
)

// ADR-004: the composite cache key must include model, system prompt,
// temperature bucket, and tenant. A collision across any of these dimensions
// is a correctness (and for tenant, a security) bug.
func TestNamespaceKey(t *testing.T) {
	base := NamespaceKey("claude-haiku-4-5", "You are helpful.", "tenant-a", 0.7)

	tests := []struct {
		name        string
		model       string
		system      string
		tenantID    string
		temperature float64
		wantSame    bool
	}{
		{"identical inputs", "claude-haiku-4-5", "You are helpful.", "tenant-a", 0.7, true},
		{"different tenant", "claude-haiku-4-5", "You are helpful.", "tenant-b", 0.7, false},
		{"different model", "claude-sonnet-4-6", "You are helpful.", "tenant-a", 0.7, false},
		{"different system prompt", "claude-haiku-4-5", "You are terse.", "tenant-a", 0.7, false},
		{"different temperature bucket", "claude-haiku-4-5", "You are helpful.", "tenant-a", 0.2, false},
		{"same temperature bucket", "claude-haiku-4-5", "You are helpful.", "tenant-a", 0.71, true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := NamespaceKey(tt.model, tt.system, tt.tenantID, tt.temperature)
			if (got == base) != tt.wantSame {
				t.Errorf("NamespaceKey() collision mismatch: got same=%v, want same=%v", got == base, tt.wantSame)
			}
		})
	}
}

func TestBucketTemperature(t *testing.T) {
	tests := []struct {
		in   float64
		want float64
	}{
		{0.0, 0.0},
		{0.71, 0.7},
		{0.75, 0.8},
		{0.04, 0.0},
		{1.0, 1.0},
	}
	for _, tt := range tests {
		if got := bucketTemperature(tt.in); got != tt.want {
			t.Errorf("bucketTemperature(%v) = %v, want %v", tt.in, got, tt.want)
		}
	}
}

func TestPgvectorLiteral(t *testing.T) {
	tests := []struct {
		name string
		in   []float32
		want string
	}{
		{"empty", nil, "[]"},
		{"single", []float32{1.5}, "[1.5]"},
		{"multiple", []float32{0.1, -0.2, 3}, "[0.1,-0.2,3]"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := pgvectorLiteral(tt.in); got != tt.want {
				t.Errorf("pgvectorLiteral(%v) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func TestNamespaceKeyIsOpaque(t *testing.T) {
	// The key must not leak its inputs (it's stored in the DB).
	key := NamespaceKey("claude-haiku-4-5", "secret system prompt", "tenant-a", 0.7)
	for _, leak := range []string{"claude", "secret", "tenant-a"} {
		if strings.Contains(key, leak) {
			t.Errorf("namespace key leaks input %q: %s", leak, key)
		}
	}
}
