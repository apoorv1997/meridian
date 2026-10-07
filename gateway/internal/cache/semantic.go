package cache

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/apoorv1997/meridian/gateway/internal/metrics"
	"github.com/apoorv1997/meridian/gateway/pkg/models"
)

// SemanticCache checks and stores prompt/response pairs using pgvector cosine similarity.
// Only operates in buffer mode — streaming bypasses the cache.
type SemanticCache struct {
	db                  *pgxpool.Pool
	similarityThreshold float64
	ttlSeconds          int
	embedder            Embedder
}

// Embedder converts text to a float32 embedding vector.
type Embedder interface {
	Embed(ctx context.Context, text string) ([]float32, error)
}

func NewSemanticCache(db *pgxpool.Pool, embedder Embedder, similarityThreshold float64, ttlSeconds int) *SemanticCache {
	return &SemanticCache{
		db:                  db,
		similarityThreshold: similarityThreshold,
		ttlSeconds:          ttlSeconds,
		embedder:            embedder,
	}
}

// NamespaceKey builds the composite cache key per ADR-004:
// hash(model_id + system_prompt + temperature_bucket + tenant_id).
// A cache hit within the wrong namespace is a security incident.
func NamespaceKey(model, systemPrompt, tenantID string, temperature float64) string {
	tempBucket := bucketTemperature(temperature)
	raw := fmt.Sprintf("%s|%s|%.1f|%s", model, systemPrompt, tempBucket, tenantID)
	sum := sha256.Sum256([]byte(raw))
	return fmt.Sprintf("%x", sum)
}

// Get attempts a cache lookup. Returns nil, nil on a miss.
func (s *SemanticCache) Get(ctx context.Context, req *models.ChatRequest, tenantID, routeID string) ([]byte, error) {
	promptText := marshalMessages(req.Messages)
	nsKey := NamespaceKey(req.Model, req.System, tenantID, req.Temperature)

	embedding, err := s.embedder.Embed(ctx, promptText)
	if err != nil {
		// Embedding failure is non-fatal — treat as a miss.
		metrics.CacheMissesTotal.WithLabelValues(routeID).Inc()
		return nil, nil
	}

	vec := pgvectorLiteral(embedding)
	const q = `
		SELECT response_text
		FROM cache_entries
		WHERE namespace_key = $1
		  AND tenant_id = $2
		  AND (expires_at IS NULL OR expires_at > now())
		  AND 1 - (prompt_embedding <=> $3::vector) >= $4
		ORDER BY prompt_embedding <=> $3::vector
		LIMIT 1`

	var responseText string
	err = s.db.QueryRow(ctx, q, nsKey, tenantID, vec, s.similarityThreshold).Scan(&responseText)
	if err != nil {
		metrics.CacheMissesTotal.WithLabelValues(routeID).Inc()
		return nil, nil
	}

	// Update hit count asynchronously.
	go s.db.Exec(context.Background(), //nolint:errcheck
		`UPDATE cache_entries SET hit_count = hit_count + 1
		 WHERE namespace_key = $1 AND tenant_id = $2`, nsKey, tenantID)

	metrics.CacheHitsTotal.WithLabelValues(routeID).Inc()
	return []byte(responseText), nil
}

// Set stores a prompt+response pair in the cache.
func (s *SemanticCache) Set(ctx context.Context, req *models.ChatRequest, tenantID string, responseBody []byte, documentIDs []string) error {
	promptText := marshalMessages(req.Messages)
	nsKey := NamespaceKey(req.Model, req.System, tenantID, req.Temperature)

	embedding, err := s.embedder.Embed(ctx, promptText)
	if err != nil {
		return fmt.Errorf("cache: embed prompt: %w", err)
	}

	vec := pgvectorLiteral(embedding)

	var expiresAt interface{}
	if s.ttlSeconds > 0 {
		expiresAt = time.Now().Add(time.Duration(s.ttlSeconds) * time.Second)
	}

	docIDs := make([]string, len(documentIDs))
	copy(docIDs, documentIDs)

	const q = `
		INSERT INTO cache_entries (tenant_id, namespace_key, prompt_text, prompt_embedding, response_text, model, document_ids, expires_at)
		VALUES ($1, $2, $3, $4::vector, $5, $6, $7, $8)
		ON CONFLICT DO NOTHING`

	_, err = s.db.Exec(ctx, q,
		tenantID, nsKey, promptText, vec, string(responseBody), req.Model, docIDs, expiresAt,
	)
	return err
}

// pgvectorLiteral converts a float32 slice to Postgres vector literal format.
func pgvectorLiteral(v []float32) string {
	if len(v) == 0 {
		return "[]"
	}
	b := make([]byte, 0, len(v)*8)
	b = append(b, '[')
	for i, f := range v {
		if i > 0 {
			b = append(b, ',')
		}
		b = fmt.Appendf(b, "%g", f)
	}
	b = append(b, ']')
	return string(b)
}

func marshalMessages(msgs []models.ChatMessage) string {
	b, _ := json.Marshal(msgs)
	return string(b)
}

// bucketTemperature rounds temperature to 1 decimal place for cache grouping.
func bucketTemperature(t float64) float64 {
	return math.Round(t*10) / 10
}
