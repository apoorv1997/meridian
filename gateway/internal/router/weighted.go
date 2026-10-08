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
func (w *WeightedRouter) Select(ctx context.Context, tenantID, routeID string, providers []string) (string, error) {
	if len(providers) == 0 {
		return "", fmt.Errorf("router: no providers to select from")
	}
	if len(providers) == 1 {
		return providers[0], nil
	}

	weights := w.loadWeights(ctx, tenantID, routeID, providers)
	return weightedRandom(providers, weights), nil
}

// weightsKey is the Redis hash holding one tenant's provider weights for one route. Route
// IDs are only unique within a tenant, so the tenant is part of the key. The eval pipeline
// writes it: route_weights_key() in eval-pipeline/meridian_eval/feedback/weight_updater.py.
func weightsKey(tenantID, routeID string) string {
	return fmt.Sprintf("route:%s:%s:weights", tenantID, routeID)
}

// loadWeights fetches per-provider weights from Redis and turns them into a probability
// distribution over providers.
func (w *WeightedRouter) loadWeights(ctx context.Context, tenantID, routeID string, providers []string) []float64 {
	raw := make([]float64, len(providers))
	present := make([]bool, len(providers))
	key := weightsKey(tenantID, routeID)
	for i, p := range providers {
		val, err := w.rdb.HGet(ctx, key, p).Float64()
		if err == nil {
			raw[i], present[i] = val, true
		}
	}
	return normalizeWeights(raw, present)
}

// normalizeWeights gives a provider with no stored weight an equal share, raises a stored
// weight below minWeight to minWeight (a provider the eval loop pushed down must not
// jump back up), and scales the result to sum to 1.0.
func normalizeWeights(raw []float64, present []bool) []float64 {
	n := len(raw)
	weights := make([]float64, n)
	var total float64
	for i := range raw {
		switch {
		case !present[i]:
			weights[i] = 1.0 / float64(n)
		case raw[i] < minWeight:
			weights[i] = minWeight
		default:
			weights[i] = raw[i]
		}
		total += weights[i]
	}
	for i := range weights {
		weights[i] /= total
	}
	return weights
}

// SetWeight persists a provider weight to Redis.
func (w *WeightedRouter) SetWeight(ctx context.Context, tenantID, routeID, provider string, weight float64) error {
	return w.rdb.HSet(ctx, weightsKey(tenantID, routeID), provider, weight).Err()
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
