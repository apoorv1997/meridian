-- Meridian — PostgreSQL 16 + pgvector schema
-- Runs once on container first init via docker-entrypoint-initdb.d

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ── Tenants ───────────────────────────────────────────────────────────────────

CREATE TABLE tenants (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    rate_limit_rpm  integer NOT NULL DEFAULT 1000,
    rate_limit_tpm  integer NOT NULL DEFAULT 100000,
    created_at      timestamptz NOT NULL DEFAULT now(),
    tenant_id       uuid NOT NULL GENERATED ALWAYS AS (id) STORED
);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
    USING (id = current_setting('app.tenant_id', true)::uuid);

-- ── API Keys ──────────────────────────────────────────────────────────────────

CREATE TABLE api_keys (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_hash    text NOT NULL UNIQUE,
    name        text NOT NULL,
    revoked_at  timestamptz,
    expires_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON api_keys
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX api_keys_key_hash_idx ON api_keys(key_hash);
CREATE INDEX api_keys_tenant_id_idx ON api_keys(tenant_id);

-- ── Route Configs ─────────────────────────────────────────────────────────────

CREATE TABLE route_configs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    route_id            text NOT NULL,
    proxy_mode          text NOT NULL DEFAULT 'buffer' CHECK (proxy_mode IN ('buffer', 'stream')),
    rag_enabled         boolean NOT NULL DEFAULT false,
    eval_rubrics        text[] NOT NULL DEFAULT '{}',
    drift_threshold     float NOT NULL DEFAULT 0.15,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, route_id)
);

ALTER TABLE route_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON route_configs
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── Provider Weights ──────────────────────────────────────────────────────────

CREATE TABLE provider_weights (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    route_id    text NOT NULL,
    provider    text NOT NULL,
    weight      float NOT NULL DEFAULT 0.5 CHECK (weight >= 0.0 AND weight <= 1.0),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, route_id, provider)
);

ALTER TABLE provider_weights ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON provider_weights
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ── Document Chunks (RAG) ─────────────────────────────────────────────────────

CREATE TABLE chunks (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL,
    document_id         uuid NOT NULL,
    content             text NOT NULL,
    content_hash        text NOT NULL,
    embedding           vector(1536),
    embedding_model     text NOT NULL,
    embedding_version   text NOT NULL,
    chunk_index         integer NOT NULL,
    metadata            jsonb NOT NULL DEFAULT '{}',
    created_at          timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON chunks
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX chunks_embedding_idx ON chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_tenant_document_idx ON chunks(tenant_id, document_id);
CREATE INDEX chunks_content_hash_idx ON chunks(content_hash);

-- ── Semantic Cache ────────────────────────────────────────────────────────────

CREATE TABLE cache_entries (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    namespace_key   text NOT NULL,
    prompt_text     text NOT NULL,
    prompt_embedding vector(1536),
    response_text   text NOT NULL,
    model           text NOT NULL,
    document_ids    uuid[] NOT NULL DEFAULT '{}',
    hit_count       integer NOT NULL DEFAULT 0,
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE cache_entries ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cache_entries
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX cache_embedding_idx ON cache_entries
    USING hnsw (prompt_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX cache_namespace_idx ON cache_entries(namespace_key);

-- ── Evaluation Results ────────────────────────────────────────────────────────

CREATE TABLE evaluation_results (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    trace_id        text NOT NULL,
    route_id        text NOT NULL,
    provider        text NOT NULL,
    tier            integer NOT NULL CHECK (tier IN (1, 2, 3)),
    latency_ms      float,
    tier1_passed    boolean,
    tier2_score     float,
    tier2_rubrics   jsonb,
    raw_judge_output text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE evaluation_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON evaluation_results
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX eval_results_trace_idx ON evaluation_results(trace_id);
CREATE INDEX eval_results_route_provider_idx ON evaluation_results(route_id, provider, created_at DESC);

-- ── Drift Embeddings (for eval pipeline) ─────────────────────────────────────

CREATE TABLE drift_embeddings (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL,
    route_id    text NOT NULL,
    embedding   vector(1536),
    window_ts   timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE drift_embeddings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON drift_embeddings
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX drift_embedding_idx ON drift_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── Cost Records ──────────────────────────────────────────────────────────────

CREATE TABLE cost_records (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    route_id        text NOT NULL,
    provider        text NOT NULL,
    model           text NOT NULL,
    input_tokens    integer NOT NULL DEFAULT 0,
    output_tokens   integer NOT NULL DEFAULT 0,
    cost_usd        float NOT NULL DEFAULT 0.0,
    cached          boolean NOT NULL DEFAULT false,
    trace_id        text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE cost_records ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cost_records
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE INDEX cost_records_tenant_created_idx ON cost_records(tenant_id, created_at DESC);

-- ── Seed default tenant for local dev ────────────────────────────────────────

INSERT INTO tenants (id, name, rate_limit_rpm, rate_limit_tpm)
VALUES ('00000000-0000-0000-0000-000000000001', 'default', 1000, 100000)
ON CONFLICT DO NOTHING;
