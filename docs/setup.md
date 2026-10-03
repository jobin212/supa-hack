# Set up Songbot

This guide takes a fresh clone through a local demo and a hosted Ando bot.
The Python package and Compute service are named `threadsong`; the agent's
display name is **Songbot**. Setup documentation checked on 2026-10-04 against
the repository and Supabase CLI 2.119.0. Compute is a private alpha, so confirm
your account has access and check `supabase compute --help` for CLI changes.

For the existing deployment, see [deployment status](deployment.md). Its database,
Storage bucket, agent, and webhook are already configured. Do not create duplicate
agents or webhooks, or reapply its table-creation SQL.

## 1. Prerequisites and local demo

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
the [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started),
a Supabase project with [Compute access](https://supabase.com/compute), and an
Ando workspace where you can invite agents and configure outbound webhooks.
The repository is private, so cloning requires GitHub access.

```sh
git clone git@github.com:jobin212/supa-hack.git
cd supa-hack
uv sync --locked
cp -n .env.example .env
chmod 600 .env
uv run threadsong serve
```

In another terminal in the same directory:

```sh
uv run threadsong demo
uv run pytest -q
uv run ruff check src tests
```

Keep `APP_MODE=demo` for this check. It uses SQLite and a test tone, requires no
credentials, and does not generate paid music. Python 3.12 is pinned in the repo;
uv can provision it. Stop the local server when finished. Hosted Compute builds
the Dockerfile remotely, so local Docker is not required for this path.

## 2. Google and Ando credentials

Create a Google API key in [AI Studio](https://aistudio.google.com/apikey) and
enable billing/model access for its project. The defaults are `gemini-3.8-flash`
for the summary and `lyria-3.5` for music, both through the Interactions API.
One key serves both calls. Check current access and pricing in Google's
[music-generation documentation](https://ai.google.dev/gemini-api/docs/music-generation).
Live song requests may incur charges; the demo above does not.

For Ando, create a dedicated external agent rather than a personal API key:

1. Open **Settings → Members → Agents → Invite agent**.
2. Give the one-time agent invite link to your agent-capable setup client and
   follow the returned enrollment instructions. Use agent enrollment, not a human
   sign-in flow. Save the resulting external-agent API key privately.
3. Name the agent **Songbot**, authenticate it, and confirm it becomes **Active**.
   Use `get_workspace_info` in the connected Ando MCP client to identify the
   workspace and the agent's `joined_as` membership.
4. Record the workspace ID and **agent membership ID** for the environment below.
   The membership ID is used to recognize native mentions.
5. Add Songbot to the demo channel using **Add members**. Record the conversation
   ID and grant access to read the thread, send replies, and update its own messages.

These steps follow Ando's [external-agent guide](https://docs.ando.so/developers/running-agents).
A provisioning key manages credentials; it is not the bot's runtime API key.
Webhook registration is a separate step below.

## 3. Link a Supabase project and create the data stores

Set this non-secret shell variable to your own project reference:

```sh
export SONGBOT_PROJECT_REF="YOUR_PROJECT_REF"
supabase login
supabase link --project-ref "$SONGBOT_PROJECT_REF"
supabase compute list --project-ref "$SONGBOT_PROJECT_REF"
```

The repository already includes `[experimental] compute = true` and the
`threadsong` service configuration. There is no need to run `supabase init` again.

On a **new project**, apply the migration:

```sh
supabase db push --dry-run
supabase db push
supabase migration list
```

The migration creates `public.song_jobs` with row-level security and no public
client access. If the table already exists but migration history is missing,
inspect the existing schema/history before proceeding; do not drop the table.

In the project's SQL Editor, run the contents of
[`supabase/setup_storage.sql`](../supabase/setup_storage.sql). This creates the
private `songs` bucket and refuses an existing public bucket with that name.
For a quick check, run:

```sql
select to_regclass('public.song_jobs') as jobs_table;
select id, public from storage.buckets where id = 'songs';
```

Expect `song_jobs` and a bucket with `public = false`.

## 4. Deploy the receiver in setup mode

Keep the hosted configuration separate from the local demo `.env`:

```sh
mkdir -p .data
cp -n .env.compute.example .data/compute.env
chmod 600 .data/compute.env
```

Edit `.data/compute.env` privately:

| Variable | Value |
| --- | --- |
| `APP_MODE` | `setup` initially, then `live` after the webhook test |
| `GEMINI_API_KEY` | Your AI Studio key |
| `ANDO_API_KEY` | Songbot's external-agent API key |
| `ANDO_WORKSPACE_ID` | Ando workspace ID |
| `ANDO_AGENT_MEMBER_ID` | Songbot's workspace membership ID |
| `ANDO_ALLOWED_CONVERSATIONS` | Demo conversation ID, or comma-separated IDs |
| `ANDO_WEBHOOK_SIGNING_SECRET` | Leave empty until the webhook is created |
| `PUBLIC_BASE_URL` | `https://YOUR_PROJECT_REF.supabase.co/compute/v1/threadsong` |

Use the actual project reference in `PUBLIC_BASE_URL`. Confirm the URL against
the deployment output. An empty conversation allowlist permits any conversation
the agent can access; use a specific channel for the demo.

The verified Compute runtime supplies `SUPABASE_DB_URL`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, and `PORT`. Do not upload `SUPABASE_` variables as
custom secrets. Do not copy the local SQLite `DATABASE_URL` or localhost URL
into hosted settings: `DATABASE_URL` would override the injected database URL.

```sh
supabase secrets set --env-file .data/compute.env --project-ref "$SONGBOT_PROJECT_REF"
uv run python scripts/prepare_compute.py
supabase compute push threadsong --project-ref "$SONGBOT_PROJECT_REF"
supabase compute status threadsong --project-ref "$SONGBOT_PROJECT_REF"
```

The preparation script stages source and manifests without `.env`, `.data`, or
slides. Always name `threadsong` when deploying; omitting the name can deploy
every configured service, including the unrelated starter.

Setup mode intentionally does not run the worker or process song requests.
Until the signing secret is set, webhook requests fail authentication. Database
readiness returns 503 in setup mode by design.

## 5. Register and verify the Ando webhook

In Ando **Studio → API & Webhooks**, create an **outbound** endpoint at:

```text
https://YOUR_PROJECT_REF.supabase.co/compute/v1/threadsong/webhooks/ando
```

Initially leave song-event delivery disabled. Copy the one-time signing secret
into `ANDO_WEBHOOK_SIGNING_SECRET` in `.data/compute.env`. It is required and is
different from the API key. Upload the updated file and redeploy:

```sh
supabase secrets set --env-file .data/compute.env --project-ref "$SONGBOT_PROJECT_REF"
supabase compute push threadsong --project-ref "$SONGBOT_PROJECT_REF"
```

Send Ando's test event and verify HTTP **204** in its delivery log. A signed
`webhook.test` works in setup mode and does not generate a song. A plain unsigned
POST should return 401. The receiver validates the raw-body HMAC and a five-minute
timestamp window. See [Ando webhooks](https://docs.ando.so/developers/webhooks).

API registration is also supported when the credential has `webhooks:write`;
the Studio route above does not require embedding administrative credentials in
the application. Register one receiver for this service to avoid duplicate delivery.

## 6. Activate and test a real song

Change `APP_MODE=live` in `.data/compute.env`, then upload and redeploy again:

```sh
supabase secrets set --env-file .data/compute.env --project-ref "$SONGBOT_PROJECT_REF"
supabase compute push threadsong --project-ref "$SONGBOT_PROJECT_REF"
curl --fail-with-body "https://${SONGBOT_PROJECT_REF}.supabase.co/compute/v1/threadsong/readyz"
```

Expect HTTP 200 with `{"ready":true}`. Then enable `message.created` delivery
on the outbound endpoint. Use a native mention in the permitted Ando channel:

> @Songbot make a celebratory song about this thread

Watch one progress reply update as the bot reads, summarizes, generates, and
uploads. It then posts a separate reply with a song link in the same thread.
Open the link to verify playback. A successful webhook delivery alone does not
prove that music generation or the reply succeeded.

```sh
supabase compute logs threadsong --project-ref "$SONGBOT_PROJECT_REF" --kind app --follow
```

The requested duration is a model hint; a 30-second target can produce a longer
song. Sharing links are bearer links: anyone with one can listen. They redirect
to a fresh five-minute download URL for the private audio file.

## 7. Updating and troubleshooting

For source updates, run `uv run python scripts/prepare_compute.py` and push
`threadsong` again. For secret changes, upload the edited private environment
file and redeploy so the process reads the new values. Avoid deploying while a
music generation is active: interrupted paid requests are not automatically repeated.

| Symptom | What to check |
| --- | --- |
| No webhook delivery | Endpoint subscription, agent channel membership, and Ando delivery log |
| Delivery returns 401 | Correct signing secret, raw request body, and timestamp |
| Mention ignored | Agent membership ID, native mention, workspace ID, conversation allowlist |
| `/readyz` returns 503 | `APP_MODE`, injected database URL, and `song_jobs` migration |
| Worker fails at startup | Missing live credentials or an accidental local `DATABASE_URL` override |
| Google 403/404 | Billing, key permissions, and configured model access |
| Google 429 | Rate/quota limits; wait or inspect the Google project's quota |
| `generation_blocked` | Google rejected the prompt; inspect the saved summary and error code |
| Generation interrupted/timed out | Inspect the job before requesting another paid generation |
| `/health` returns gateway 503 | This occurred while `/readyz` and signed webhook tests worked; check both |

Inspect job progress without printing credentials or sharing tokens:

```sql
select id, status, stage, error_code, to_timestamp(updated_at) as updated_at
from public.song_jobs
order by updated_at desc
limit 20;
```

For local live development, use `.env.example` with `APP_MODE=live`, an HTTPS
tunnel URL, a direct/session-pooler Postgres connection, and a server-side
Supabase secret key. Those credentials are needed locally because local Python
does not receive Compute's injected variables. Keep only one active worker
against the hosted queue during debugging; `RUN_WORKER=false` disables the local one.

The automated test suite uses synthetic data. Scripts named `smoke_music.py`
or `lyria_prompt_lab.py` can make paid calls; read their instructions first.
Never commit real environment files, `.data`, enrollment responses, or signed
download URLs. Only the blank `.env.example` and `.env.compute.example` templates
belong in Git.
