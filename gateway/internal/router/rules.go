package router

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/apoorv1997/meridian/gateway/internal/config"
	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

// RouteRule is the resolved routing configuration for a single route.
type RouteRule struct {
	RouteID    string
	ProxyMode  string // "buffer" | "stream"
	RAGEnabled bool
	Providers  []string // ordered list of eligible providers
}

// RulesLoader resolves route rules from PostgreSQL with a fallback to defaults.
type RulesLoader struct {
	db  *pgxpool.Pool
	cfg *config.ProvidersConfig
}

func NewRulesLoader(db *pgxpool.Pool, cfg *config.ProvidersConfig) *RulesLoader {
	return &RulesLoader{db: db, cfg: cfg}
}

// Load returns the route rule for the given tenant+route, or a default rule.
func (r *RulesLoader) Load(ctx context.Context, tenantID, routeID string) (*RouteRule, error) {
	const q = `
		SELECT route_id, proxy_mode, rag_enabled
		FROM route_configs
		WHERE tenant_id = $1 AND route_id = $2
		LIMIT 1`

	var rule RouteRule
	err := r.db.QueryRow(ctx, q, tenantID, routeID).Scan(
		&rule.RouteID, &rule.ProxyMode, &rule.RAGEnabled,
	)
	if err != nil {
		// No config found — use defaults
		rule = RouteRule{
			RouteID:   routeID,
			ProxyMode: "buffer",
		}
	}

	rule.Providers = r.enabledProviders()
	if len(rule.Providers) == 0 {
		return nil, fmt.Errorf("router: no providers configured")
	}
	return &rule, nil
}

func (r *RulesLoader) enabledProviders() []string {
	var providers []string
	if r.cfg.Anthropic.Enabled {
		providers = append(providers, models.ProviderAnthropic)
	}
	if r.cfg.OpenAI.Enabled {
		providers = append(providers, models.ProviderOpenAI)
	}
	if r.cfg.Google.Enabled {
		providers = append(providers, models.ProviderGoogle)
	}
	return providers
}
