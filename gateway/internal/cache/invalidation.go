package cache

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Invalidator removes cache entries that reference a given document.
// Called by the RAG engine after a document update event.
type Invalidator struct {
	db *pgxpool.Pool
}

func NewInvalidator(db *pgxpool.Pool) *Invalidator {
	return &Invalidator{db: db}
}

// ByDocumentID deletes all cache entries that include documentID in their
// document_ids array for the given tenant.
func (inv *Invalidator) ByDocumentID(ctx context.Context, tenantID, documentID string) (int64, error) {
	const q = `
		DELETE FROM cache_entries
		WHERE tenant_id = $1
		  AND $2::uuid = ANY(document_ids)`

	tag, err := inv.db.Exec(ctx, q, tenantID, documentID)
	if err != nil {
		return 0, fmt.Errorf("cache invalidation: %w", err)
	}
	return tag.RowsAffected(), nil
}

// ByNamespace deletes all cache entries for a given namespace key.
// Useful for force-clearing a tenant/model/system-prompt combination.
func (inv *Invalidator) ByNamespace(ctx context.Context, tenantID, namespaceKey string) (int64, error) {
	const q = `DELETE FROM cache_entries WHERE tenant_id = $1 AND namespace_key = $2`
	tag, err := inv.db.Exec(ctx, q, tenantID, namespaceKey)
	if err != nil {
		return 0, fmt.Errorf("cache invalidation: %w", err)
	}
	return tag.RowsAffected(), nil
}

// Expired removes entries past their expires_at timestamp (for maintenance).
func (inv *Invalidator) Expired(ctx context.Context) (int64, error) {
	const q = `DELETE FROM cache_entries WHERE expires_at IS NOT NULL AND expires_at < now()`
	tag, err := inv.db.Exec(ctx, q)
	if err != nil {
		return 0, fmt.Errorf("cache invalidation: %w", err)
	}
	return tag.RowsAffected(), nil
}
