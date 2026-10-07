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

func TestWeightsKeyMatchesEvalPipeline(t *testing.T) {
	// route_weights_key() in eval-pipeline/meridian_eval/feedback/weight_updater.py writes this key.
	if got := weightsKey("r1"); got != "route:r1:weights" {
		t.Errorf("weightsKey = %q, want %q", got, "route:r1:weights")
	}
}

func TestNormalizeWeights(t *testing.T) {
	cases := []struct {
		name    string
		raw     []float64
		present []bool
		want    []float64
	}{
		{"stored weights pass through", []float64{0.7, 0.3}, []bool{true, true}, []float64{0.7, 0.3}},
		{"no stored weights: equal shares", []float64{0, 0, 0}, []bool{false, false, false}, []float64{1.0 / 3, 1.0 / 3, 1.0 / 3}},
		// A provider pushed under the floor stays near the floor; it must not jump to an equal share.
		{"below floor is raised to the floor", []float64{0.04, 0.48, 0.48}, []bool{true, true, true}, []float64{0.05 / 1.01, 0.48 / 1.01, 0.48 / 1.01}},
		{"negative is raised to the floor", []float64{-1, 0.95}, []bool{true, true}, []float64{0.05, 0.95}},
		{"missing provider joins with an equal share", []float64{0.6, 0}, []bool{true, false}, []float64{0.6 / 1.1, 0.5 / 1.1}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := normalizeWeights(tc.raw, tc.present)
			var sum float64
			for i := range got {
				sum += got[i]
				if math.Abs(got[i]-tc.want[i]) > 1e-9 {
					t.Errorf("weight[%d] = %.6f, want %.6f", i, got[i], tc.want[i])
				}
			}
			if math.Abs(sum-1) > 1e-9 {
				t.Errorf("weights sum to %.9f, want 1", sum)
			}
		})
	}
}
