-- ── HomeManager — PostgreSQL init script ───────────────────────────────
-- Loaded automatically by Docker's postgres entrypoint on first start.

-- Create roles if they don't exist (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'hmuser') THEN
        CREATE ROLE hmuser WITH LOGIN PASSWORD 'hmpassword';
    END IF;
END
$$;

-- Create the database if it does not already exist
SELECT 'CREATE DATABASE homemanager'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'homemanager')
\gexec
