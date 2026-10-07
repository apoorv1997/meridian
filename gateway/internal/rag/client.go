// Package rag is the gateway's HTTP client for the RAG engine retrieval
// service. It provides query-time context retrieval and the embedder that
// backs the semantic cache (same embedding model as the indexed chunks,
// per ADR-008).
package rag

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func NewClient(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		http:    &http.Client{Timeout: 10 * time.Second},
	}
}

// ── /embed — used by the gateway semantic cache ──────────────────────────────

type embedRequest struct {
	Texts []string `json:"texts"`
}

type embedResponse struct {
	Embeddings [][]float32 `json:"embeddings"`
	Model      string      `json:"model"`
	Version    string      `json:"version"`
}

// Embed implements cache.Embedder.
func (c *Client) Embed(ctx context.Context, text string) ([]float32, error) {
	var resp embedResponse
	if err := c.post(ctx, "/embed", embedRequest{Texts: []string{text}}, &resp); err != nil {
		return nil, fmt.Errorf("rag: embed: %w", err)
	}
	if len(resp.Embeddings) != 1 {
		return nil, fmt.Errorf("rag: embed returned %d vectors, want 1", len(resp.Embeddings))
	}
	return resp.Embeddings[0], nil
}

// ── /retrieve — query-time RAG context ────────────────────────────────────────

type retrieveRequest struct {
	Query    string `json:"query"`
	TenantID string `json:"tenant_id"`
	TopK     int    `json:"top_k,omitempty"`
	TopN     int    `json:"top_n,omitempty"`
}

// RetrievalResult carries the assembled context and the source document IDs
// used to tag cache entries for invalidation.
type RetrievalResult struct {
	Context     string   `json:"context"`
	DocumentIDs []string `json:"document_ids"`
}

func (c *Client) Retrieve(ctx context.Context, query, tenantID string) (*RetrievalResult, error) {
	var resp RetrievalResult
	err := c.post(ctx, "/retrieve", retrieveRequest{Query: query, TenantID: tenantID}, &resp)
	if err != nil {
		return nil, fmt.Errorf("rag: retrieve: %w", err)
	}
	return &resp, nil
}

func (c *Client) Healthy(ctx context.Context) bool {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/health", nil)
	if err != nil {
		return false
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

func (c *Client) post(ctx context.Context, path string, in, out any) error {
	payload, err := json.Marshal(in)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("%s returned %d: %s", path, resp.StatusCode, body)
	}
	return json.NewDecoder(resp.Body).Decode(out)
}
