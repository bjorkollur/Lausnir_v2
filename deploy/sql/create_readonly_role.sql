-- Read-only role for the MCP server (docs/superpowers/specs/2026-09-29-mcp-server-design.md §5).
-- Run once as the DB owner:
--   psql "$SYNC_URL" -v ro_password='…' -f deploy/sql/create_readonly_role.sql
-- Re-runnable: CREATE ROLE is guarded, every other statement is idempotent.
-- (\gexec runs the generated CREATE ROLE only when the row exists; a DO block
--  cannot see psql variables inside dollar quotes, hence this form.)
SELECT format('CREATE ROLE lausnir_ro LOGIN PASSWORD %L', :'ro_password') AS stmt
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lausnir_ro') \gexec

GRANT CONNECT ON DATABASE lausnir_v2 TO lausnir_ro;
GRANT USAGE ON SCHEMA public TO lausnir_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO lausnir_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO lausnir_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO lausnir_ro;
REVOKE CREATE ON SCHEMA public FROM lausnir_ro;
-- TEMP is granted to PUBLIC by default, so revoking it from lausnir_ro alone is a no-op;
-- it must come off PUBLIC. The superuser owner keeps TEMP regardless (superusers bypass ACLs).
REVOKE TEMP ON DATABASE lausnir_v2 FROM PUBLIC;
ALTER ROLE lausnir_ro SET default_transaction_read_only = on;
ALTER ROLE lausnir_ro SET statement_timeout = '15s';
