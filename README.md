# Songbot (Threadsong)

Hackathon project, now retired from active development. To return later, start
with the [retirement and restart runbook](docs/retirement.md). This is a development
status, not a shutdown: Compute was still running at the 2026-10-04 handoff.

[Hackathon submission and Loom demo](docs/hackathon.md) ·
[Fresh setup](docs/setup.md) · [Deployment history](docs/deployment.md)

Mention an Ando agent in a thread. It reads the conversation, summarizes it with
Gemini, generates lyrics and music with Lyria, and replies in the same thread with a stable
sharing link. Slack can be added through the platform adapter interface.

**Hosting:** one Python 3.12 + uv service on Supabase Compute, with Supabase
Postgres for durable jobs and private Supabase Storage for audio. The Compute
service runs FastAPI and a background worker together. No separate Python host,
Edge Functions, or user-managed VM is required.

Start with the [complete setup guide](docs/setup.md), from a fresh clone through
credentials, database setup, Compute deployment, Ando webhook registration, and
the first song. Use [.env.example](.env.example) locally and
[.env.compute.example](.env.compute.example) for hosted configuration.

See [the architecture and request flow](docs/architecture.md).

## Demo slides and diagrams

<p>
  <a href="docs/slides/01-songbot-opening.png"><img src="docs/slides/01-songbot-opening.png" width="200" alt="Songbot opening slide"></a>
  <a href="docs/slides/02-songbot-data-flow.png"><img src="docs/slides/02-songbot-data-flow.png" width="200" alt="Simple Songbot data flow"></a>
  <a href="docs/slides/03-songbot-closing.png"><img src="docs/slides/03-songbot-closing.png" width="200" alt="Songbot closing slide"></a>
</p>

Download the [mobile PNG slides](docs/slides/README.md),
[editable PowerPoint](docs/slides/songbot-mobile.pptx), or
[Supabase architecture diagram](docs/diagrams/threadsong-mobile.png).

## Run without credentials

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
uv sync --locked
cp -n .env.example .env
uv run threadsong serve
```

In another terminal:

```sh
uv run threadsong demo
```

The demo runs the complete queue, storage, and sharing flow with a canned thread
and a **one-second test tone**, not generated music. It uses SQLite and local
files under `.data/`, makes no external API calls, and returns a playable link.
Live webhooks are disabled in demo mode; demo endpoints are absent in live mode.

```sh
uv run pytest -q
uv run ruff check src tests
```

## Configure the real Ando bot

1. Create a dedicated Ando agent/API key with permission to read the intended
   conversations and send messages. Add the agent to those conversations.
2. Fill in `.env` using `.env.example`: select `APP_MODE=live`, set the Ando
   workspace and agent membership IDs, API key and webhook signing secret,
   Google API key, Supabase URL and service-role key, and the Postgres URL.
   Use a direct or session-pooler Postgres connection with TLS. Standard
   `postgresql://…` URLs are adapted to Python's `asyncpg` format automatically.
   When `DATABASE_URL` is unset, the app also accepts Compute's `SUPABASE_DB_URL`.
   Set `PUBLIC_BASE_URL` to the final HTTPS Compute URL.
3. Apply `supabase/migrations/20261003194616_threadsong_jobs.sql` to the project
   with your normal Supabase migration workflow (`supabase db push`). Then run
   `supabase/setup_storage.sql` in the project's SQL editor to provision the
   private `songs` bucket. The SQL refuses an existing public bucket. The job
   table has RLS enabled and grants no access to anonymous or signed-in clients.
4. Deploy with runtime secrets as described below. Register an Ando webhook for
   `message.created` at `https://YOUR-COMPUTE-URL/webhooks/ando`.
5. Native Ando mentions (`<!workspace_membership:AGENT_MEMBER_ID|Display Name>`)
   are recognized by agent membership ID. `ANDO_MENTION_TOKEN` defaults to
   `@Songbot`; that literal token and `<@AGENT_MEMBER_ID>` also work. Optionally
   restrict the conversation allowlist before enabling the webhook.
6. Mention the bot in a test thread, for example `@Songbot make this a country
   song`. Verify acknowledgment, one generation, a reply in the original thread,
   and playback from the sharing link.

The Google adapter targets the Interactions API (`gemini-3.8-flash` for a brief summary,
`lyria-3.5` for lyrics and music). Gemini preserves who did what and the intended
mood in one or two sentences. An explicitly requested style is passed through;
otherwise Lyria chooses it. We do not supply verses or a chorus. The short title
is used only for the chat reply. Models are configurable. Your key must have model access.
The requested 30-second duration is guidance to the model.

Keys stay in gitignored `.env` locally and in runtime secrets when hosted.
`.env` is excluded from both the Compute upload and Docker build context.
Never bake credentials into the image or a frontend.

## Deploy to Supabase Compute

Project: `supa-hack-1` (`vgmkqcatnmmfhkvzsiwb`). CLI 2.119.0 is installed on this
Mac at `~/.local/bin/supabase`. Compute is deployed in live mode, database
readiness passes, and signed Ando webhook delivery has been verified. Songbot
is enabled for `#jobin-test`; a real user mention has completed generation,
private storage, the original-thread reply, and a valid audio download. See
[deployment status](docs/deployment.md) for the current state.

`supabase/config.toml` defines `threadsong` as one public 2 GB Dockerfile service
built from a generated source directory. Compute refuses the repository root as
its source; the preparation script copies only application source and manifests
into `supabase/compute/threadsong/`. The container listens on the injected `PORT`
and defaults to `APP_MODE=setup`. The original `hello-compute` starter is preserved.

```sh
supabase compute list --project-ref vgmkqcatnmmfhkvzsiwb
uv run python scripts/prepare_compute.py
supabase compute push threadsong --project-ref vgmkqcatnmmfhkvzsiwb
supabase compute status threadsong --project-ref vgmkqcatnmmfhkvzsiwb
supabase compute logs threadsong --project-ref vgmkqcatnmmfhkvzsiwb
```

To follow job stages and timings as requests run:

```sh
supabase compute logs threadsong --project-ref vgmkqcatnmmfhkvzsiwb --kind app --follow
```

Logs include job IDs, stages, elapsed time, and status, without thread text,
credentials, or song access tokens. The CLI polls for new log lines every ten seconds.

Always name `threadsong` when pushing; omitting names can deploy every service.
Compute builds the image remotely, so this workflow does not need local Docker.

**Verified runtime secrets:** `supabase secrets set --env-file <private-file>`
supplies project secrets to Compute. The deployed receiver accepted a webhook
signed with the uploaded secret and rejected an unsigned request. Keep the file
outside the generated build context. Do not upload your local demo `.env` as-is:
its SQLite URL and demo settings are not the hosted configuration. The CLI
reserves `SUPABASE_` names for platform-provided values. The live deployment
uses the injected database URL, project URL, and service-role key. After changing
runtime secrets, redeploy the service so a new process picks up the configuration.

In `setup` mode, only signed `webhook.test` events return 204. Song requests
return 503, `/readyz` returns 503, and no worker runs. Enable `message.created`
delivery only after configuring `APP_MODE=live` and verifying database readiness.
Still verify that idle suspension does not interrupt a real music generation.
Postgres leases recover queued work once the process runs again, but do not
themselves wake a suspended service.

Health checks: `GET /health` for the process and `GET /readyz` for database/schema
readiness. Sharing links resolve through `/s/<random-token>` to short-lived
Storage URLs. Anyone holding a sharing link can listen.

## Reliability and scope

- A signed webhook is acknowledged only after its request is saved. Duplicate
  deliveries reuse the same job. Bot messages and unapproved workspaces are ignored.
- Leased Postgres jobs survive restarts. Thread replies use stable Ando
  idempotency keys. Raw thread text is removed after drafting or terminal failure.
- A single status reply updates as the thread is read and summarized,
  music is generated, and audio is uploaded. Its message ID is saved with the
  job so restarts reuse it. Status calls time out after five seconds and failures
  do not stop generation. The final song arrives in a separate notifying reply.
- An interrupted or timed-out music request may already have incurred a charge.
  The worker records failure instead of automatically purchasing another song.
  Inspect that outcome before submitting a new explicit request.
- Provider rejections retain their HTTP status and safe error code. Ando explains
  content-filter blocks, rate/quota limits, and access/configuration failures;
  prompts, credentials, and raw provider error messages are not logged.
- MVP: one tenant, one serial music worker, Ando mentions, audio sharing. Slack,
  reactions, Notion enrichment, OAuth, and a frontend are future additions.

## Optional real Postgres check

The experimental Docker-free stack was tested on this Mac. To start only Postgres:

```sh
supabase start --runtime native --exclude rest,auth,realtime,storage,functions,studio,mail,analytics,pooler --eager
THREADSONG_TEST_POSTGRES_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres uv run pytest tests/test_postgres.py -q
```

The integration check requires a migrated, empty local `song_jobs` table and
cleans up only its own rows. It verifies real Postgres claim concurrency,
deduplication, expired-lease fencing, RLS, and client-role privileges.
Use `supabase stop` to stop the local stack. Storage provisioning is separate
so these database checks can run without the other local Supabase services.

Earlier account and CLI setup notes are in [SESSION_SUMMARY.md](SESSION_SUMMARY.md).
