package auth

import (
	"context"
	"crypto/sha256"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"

	gwerrors "github.com/apoorv1997/meridian/gateway/pkg/errors"
	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

// Authenticator validates API keys against PostgreSQL.
type Authenticator struct {
	db *pgxpool.Pool
}

func NewAuthenticator(db *pgxpool.Pool) *Authenticator {
	return &Authenticator{db: db}
}

// AuthResult holds the data extracted from a valid API key.
type AuthResult struct {
	TenantID string
	KeyID    string
	KeyName  string
}

// Authenticate hashes the raw key and looks it up in the api_keys table.
// Returns ErrUnauthorized for any invalid/expired/revoked key.
func (a *Authenticator) Authenticate(ctx context.Context, rawKey string) (*AuthResult, error) {
	if rawKey == "" {
		return nil, gwerrors.ErrUnauthorized
	}

	hash := hashKey(rawKey)

	// Use a superuser query (bypasses RLS) — we're authenticating, not yet in a tenant context.
	const q = `
		SELECT ak.id, ak.tenant_id, ak.name
		FROM api_keys ak
		WHERE ak.key_hash = $1
		  AND ak.revoked_at IS NULL
		  AND (ak.expires_at IS NULL OR ak.expires_at > now())`

	var result AuthResult
	err := a.db.QueryRow(ctx, q, hash).Scan(&result.KeyID, &result.TenantID, &result.KeyName)
	if err != nil {
		return nil, gwerrors.ErrUnauthorized
	}

	return &result, nil
}

// SetTenantContext sets the PostgreSQL session variable used by RLS policies.
// Call this on every connection before any tenant-scoped query.
func SetTenantContext(ctx context.Context, conn interface {
	Exec(ctx context.Context, sql string, args ...any) (interface{}, error)
}, tenantID string) error {
	_, err := conn.Exec(ctx, fmt.Sprintf("SET app.tenant_id = '%s'", tenantID))
	return err
}

func hashKey(rawKey string) string {
	sum := sha256.Sum256([]byte(rawKey))
	return fmt.Sprintf("%x", sum)
}

// TenantIDFromContext extracts the tenant_id set by the auth middleware.
func TenantIDFromContext(ctx context.Context) string {
	v, _ := ctx.Value(models.ContextKeyTenantID).(string)
	return v
}

// APIKeyIDFromContext extracts the api_key_id set by the auth middleware.
func APIKeyIDFromContext(ctx context.Context) string {
	v, _ := ctx.Value(models.ContextKeyAPIKeyID).(string)
	return v
}
