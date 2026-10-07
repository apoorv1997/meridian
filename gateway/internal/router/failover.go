package router

import (
	"context"
	"fmt"

	"github.com/apoorv1997/meridian/gateway/internal/circuit"
	gwerrors "github.com/apoorv1997/meridian/gateway/pkg/errors"
)

// FailoverRouter wraps WeightedRouter with circuit-breaker-aware failover.
type FailoverRouter struct {
	weighted *WeightedRouter
	breaker  *circuit.Breaker
}

func NewFailoverRouter(weighted *WeightedRouter, breaker *circuit.Breaker) *FailoverRouter {
	return &FailoverRouter{weighted: weighted, breaker: breaker}
}

// Pick returns a provider that has an open circuit, trying each candidate in
// weighted order. Returns ErrNoProviders if all circuits are open.
func (f *FailoverRouter) Pick(ctx context.Context, routeID string, providers []string) (string, error) {
	// Build ordered candidate list by sampling the weighted router repeatedly,
	// falling back to round-robin for remaining providers.
	ordered := f.orderedCandidates(ctx, routeID, providers)

	for _, provider := range ordered {
		if f.breaker.Allow(ctx, provider) {
			return provider, nil
		}
	}

	return "", fmt.Errorf("router: %w (all %d providers circuit-open)",
		gwerrors.ErrNoProviders, len(providers))
}

// orderedCandidates returns providers sorted by descending weight, deduplicated.
func (f *FailoverRouter) orderedCandidates(ctx context.Context, routeID string, providers []string) []string {
	seen := make(map[string]bool, len(providers))
	ordered := make([]string, 0, len(providers))

	// Sample the weighted router len(providers) times to get a weight-ordered list.
	for range providers {
		p, err := f.weighted.Select(ctx, routeID, providers)
		if err != nil {
			break
		}
		if !seen[p] {
			seen[p] = true
			ordered = append(ordered, p)
		}
	}

	// Append any providers not yet seen (all had equal weight, or sampling missed them).
	for _, p := range providers {
		if !seen[p] {
			ordered = append(ordered, p)
		}
	}
	return ordered
}
