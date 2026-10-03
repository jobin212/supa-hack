-- One durable work table, accessed only by the trusted Compute backend.
CREATE TABLE public.song_jobs (
    id varchar(36) PRIMARY KEY,
    platform varchar(32) NOT NULL,
    workspace_id varchar(200) NOT NULL,
    message_id varchar(200) NOT NULL,
    request json NOT NULL,
    status varchar(32) NOT NULL DEFAULT 'pending',
    stage varchar(32) NOT NULL DEFAULT 'context',
    payload json NOT NULL DEFAULT '{}',
    attempts integer NOT NULL DEFAULT 0,
    available_at double precision NOT NULL,
    lease_until double precision,
    lease_token varchar(36),
    created_at double precision NOT NULL,
    updated_at double precision NOT NULL,
    share_token varchar(64) NOT NULL UNIQUE,
    error_code varchar(100),
    CONSTRAINT song_jobs_source_key UNIQUE (platform, workspace_id, message_id)
);

CREATE INDEX song_jobs_due_idx ON public.song_jobs (status, available_at, lease_until);
ALTER TABLE public.song_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.song_jobs FROM PUBLIC, anon, authenticated;
-- No public Data API policies. Compute uses a trusted Postgres server credential.

-- Bucket provisioning is separate in setup_storage.sql, so a Postgres-only
-- Docker-free local stack can run the application schema and queue tests.
