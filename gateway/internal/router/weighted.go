package router

import (
	"context"
	"fmt"
	"math/rand"

	"github.com/redis/go-redis/v9"
)

const minWeight = 0.05 // floor per ADR-005

// WeightedRouter selects a provider by weighted random using weights from Redis.
type WeightedRouter struct {
	rdb *redis.Client
}

func NewWeightedRouter(rdb *redis.Client) *WeightedRouter {
	return &WeightedRouter{rdb: rdb}
}

// Select returns one provider name chosen by weighted random.
// Falls back to uniform random if no Redis weights exist.
func (w *WeightedRouter) Select(ctx context.Context, routeID string, providers []string) (string, error) {
	if len(providers) == 0 {
		return "", fmt.Errorf("router: no providers to select from")
	}
	if len(providers) == 1 {
		return providers[0], nil
	}

	weights := w.loadWeights(ctx, routeID, providers)
	return weightedRandom(providers, weights), nil
}

// loadWeights fetches per-provider weights from Redis.
// Missing entries get a uniform share, clamped to minWeight.
func (w *WeightedRouter) loadWeights(ctx context.Context, routeID string, providers []string) []float64 {
	weights := make([]float64, len(providers))
	key := fmt.Sprintf("route:%s:weights", routeID)

	for i, p := range providers {
		val, err := w.rdb.HGet(ctx, key, p).Float64()
		if err != nil || val < minWeight {
			val = 1.0 / float64(len(providers))
		}
		weights[i] = val
	}

	// Normalise so they sum to 1.0
	var total float64
	for _, wt := range weights {
		total += wt
	}
	if total > 0 {
		for i := range weights {
			weights[i] /= total
		}
	}
	return weights
}

// SetWeight persists a provider weight to Redis.
func (w *WeightedRouter) SetWeight(ctx context.Context, routeID, provider string, weight float64) error {
	key := fmt.Sprintf("route:%s:weights", routeID)
	return w.rdb.HSet(ctx, key, provider, weight).Err()
}

func weightedRandom(providers []string, weights []float64) string {
	r := rand.Float64()
	cumulative := 0.0
	for i, w := range weights {
		cumulative += w
		if r < cumulative {
			return providers[i]
		}
	}
	return providers[len(providers)-1]
}
