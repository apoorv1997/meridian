# ADR-009: Row-level security for multi-tenancy

**Status:** Accepted

## Context
Meridian is multi-tenant. Isolation enforced only in application code fails the first time one query
forgets its `WHERE tenant_id = ...`.

## Decision
Enforce tenant isolation in PostgreSQL itself. Every table has `tenant_id uuid NOT NULL`, has row-level
security enabled, and has a `tenant_isolation` policy:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <table>
    USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

Every connection sets `app.tenant_id` before it queries. A new table gets RLS and its policy before any
code reads or writes it. This is not optional.

## Consequences
- A query that forgets to filter by tenant returns nothing rather than another tenant's data.
- Forgetting to set the tenant context is a bug that shows up immediately, not a silent leak.
- An integration test runs against a real PostgreSQL container and checks that one tenant cannot read
  another tenant's rows.
