package tests

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	tcpostgres "github.com/testcontainers/testcontainers-go/modules/postgres"
)

const (
	tenantA = "00000000-0000-0000-0000-00000000000a"
	tenantB = "00000000-0000-0000-0000-00000000000b"
)

// rlsTables lists every tenant-scoped table. ADR-009: each must have RLS
// enabled with a tenant_isolation policy — verified table by table below.
var rlsTables = []string{
	"api_keys",
	"route_configs",
	"provider_weights",
	"chunks",
	"cache_entries",
	"evaluation_results",
	"drift_embeddings",
	"cost_records",
}

// startPostgres spins up pgvector-enabled Postgres with the real init.sql schema.
func startPostgres(t *testing.T) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()

	initSQL, err := filepath.Abs("../../infra/docker/postgres/init.sql")
	if err != nil {
		t.Fatal(err)
	}

	container, err := tcpostgres.Run(ctx,
		"pgvector/pgvector:pg16",
		tcpostgres.WithInitScripts(initSQL),
		tcpostgres.WithDatabase("meridian"),
		tcpostgres.WithUsername("meridian"),
		tcpostgres.WithPassword("test"),
		tcpostgres.BasicWaitStrategies(),
	)
	if err != nil {
		t.Fatalf("failed to start postgres container: %v", err)
	}
	t.Cleanup(func() { container.Terminate(context.Background()) }) //nolint:errcheck

	dsn, err := container.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return pool
}

// rlsPool opens a NON-superuser connection pool. RLS does not apply to the
// table owner / superuser, so policies must be tested as a regular role.
func rlsPool(t *testing.T, admin *pgxpool.Pool, dsnTemplate string) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()

	_, err := admin.Exec(ctx, `
		DO $$ BEGIN
			CREATE ROLE app_user LOGIN PASSWORD 'app_test';
		EXCEPTION WHEN duplicate_object THEN NULL; END $$;
		GRANT USAGE ON SCHEMA public TO app_user;
		GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
	`)
	if err != nil {
		t.Fatal(err)
	}

	pool, err := pgxpool.New(ctx, dsnTemplate)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func TestRowLevelSecurity(t *testing.T) {
	if testing.Short() {
		t.Skip("requires docker")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()

	admin := startPostgres(t)

	// Extract host/port from the admin pool config to build the app_user DSN.
	cfg := admin.Config().ConnConfig
	appDSN := fmt.Sprintf("postgresql://app_user:app_test@%s:%d/meridian?sslmode=disable",
		cfg.Host, cfg.Port)

	// Seed two tenants as admin (tenants table policy keys on id = current tenant).
	_, err := admin.Exec(ctx,
		`INSERT INTO tenants (id, name) VALUES ($1, 'tenant-a'), ($2, 'tenant-b')`,
		tenantA, tenantB)
	if err != nil {
		t.Fatal(err)
	}

	app := rlsPool(t, admin, appDSN)

	t.Run("every tenant-scoped table has RLS enabled with a policy", func(t *testing.T) {
		for _, table := range rlsTables {
			var rlsEnabled bool
			err := admin.QueryRow(ctx,
				`SELECT relrowsecurity FROM pg_class WHERE relname = $1`, table,
			).Scan(&rlsEnabled)
			if err != nil {
				t.Fatalf("%s: %v", table, err)
			}
			if !rlsEnabled {
				t.Errorf("ADR-009 violation: table %s does not have RLS enabled", table)
			}

			var policyCount int
			err = admin.QueryRow(ctx,
				`SELECT count(*) FROM pg_policies WHERE tablename = $1 AND policyname = 'tenant_isolation'`,
				table,
			).Scan(&policyCount)
			if err != nil {
				t.Fatal(err)
			}
			if policyCount == 0 {
				t.Errorf("ADR-009 violation: table %s has no tenant_isolation policy", table)
			}
		}
	})

	t.Run("tenant cannot read another tenant's rows", func(t *testing.T) {
		// Insert a cost record for tenant A (as tenant A).
		conn, err := app.Acquire(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Release()

		if _, err := conn.Exec(ctx, fmt.Sprintf("SET app.tenant_id = '%s'", tenantA)); err != nil {
			t.Fatal(err)
		}
		_, err = conn.Exec(ctx, `
			INSERT INTO cost_records (tenant_id, route_id, provider, model, cost_usd)
			VALUES ($1, 'default', 'anthropic', 'claude-haiku-4-5', 0.001)`, tenantA)
		if err != nil {
			t.Fatal(err)
		}

		// Tenant A sees its own row.
		var count int
		if err := conn.QueryRow(ctx, `SELECT count(*) FROM cost_records`).Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != 1 {
			t.Fatalf("tenant A should see its own row, got %d rows", count)
		}

		// Switch session to tenant B — the row must disappear.
		if _, err := conn.Exec(ctx, fmt.Sprintf("SET app.tenant_id = '%s'", tenantB)); err != nil {
			t.Fatal(err)
		}
		if err := conn.QueryRow(ctx, `SELECT count(*) FROM cost_records`).Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != 0 {
			t.Errorf("SECURITY: tenant B can read %d of tenant A's cost_records rows", count)
		}
	})

	t.Run("tenant cannot insert rows for another tenant", func(t *testing.T) {
		conn, err := app.Acquire(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Release()

		if _, err := conn.Exec(ctx, fmt.Sprintf("SET app.tenant_id = '%s'", tenantB)); err != nil {
			t.Fatal(err)
		}
		// Session is tenant B but row claims tenant A — policy must reject it.
		_, err = conn.Exec(ctx, `
			INSERT INTO cost_records (tenant_id, route_id, provider, model, cost_usd)
			VALUES ($1, 'default', 'anthropic', 'claude-haiku-4-5', 0.001)`, tenantA)
		if err == nil {
			t.Error("SECURITY: tenant B inserted a row owned by tenant A")
		}
	})

	t.Run("query without tenant context sees nothing", func(t *testing.T) {
		conn, err := app.Acquire(ctx)
		if err != nil {
			t.Fatal(err)
		}
		defer conn.Release()

		// Fresh connection, no SET app.tenant_id. current_setting(..., true)
		// returns NULL → policy predicate is NULL → no rows visible.
		var count int
		if err := conn.QueryRow(ctx, `SELECT count(*) FROM cost_records`).Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != 0 {
			t.Errorf("SECURITY: %d rows visible without tenant context", count)
		}
	})
}
