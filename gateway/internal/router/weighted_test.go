package router

import (
	"math"
	"testing"
)

func TestWeightedRandomDistribution(t *testing.T) {
	providers := []string{"anthropic", "openai"}
	weights := []float64{0.8, 0.2}

	const n = 100_000
	counts := map[string]int{}
	for i := 0; i < n; i++ {
		counts[weightedRandom(providers, weights)]++
	}

	gotAnthropic := float64(counts["anthropic"]) / n
	if math.Abs(gotAnthropic-0.8) > 0.02 {
		t.Errorf("anthropic selected %.3f of the time, want ~0.80", gotAnthropic)
	}
	if counts["anthropic"]+counts["openai"] != n {
		t.Errorf("selections outside provider set: %v", counts)
	}
}

func TestWeightedRandomEdgeCases(t *testing.T) {
	t.Run("single provider always selected", func(t *testing.T) {
		for i := 0; i < 100; i++ {
			if got := weightedRandom([]string{"anthropic"}, []float64{1.0}); got != "anthropic" {
				t.Fatalf("got %q, want anthropic", got)
			}
		}
	})

	t.Run("zero-weight provider never selected", func(t *testing.T) {
		providers := []string{"dead", "live"}
		weights := []float64{0.0, 1.0}
		for i := 0; i < 1000; i++ {
			if got := weightedRandom(providers, weights); got == "dead" {
				t.Fatal("zero-weight provider was selected")
			}
		}
	})

	t.Run("falls back to last provider on rounding", func(t *testing.T) {
		// Weights that don't quite sum to 1.0 must still return a provider.
		got := weightedRandom([]string{"a", "b"}, []float64{0.0, 0.0})
		if got != "a" && got != "b" {
			t.Errorf("got %q, want a member of the provider set", got)
		}
	})
}
