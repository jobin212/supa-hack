# Retiring and restarting Songbot

Handoff: **2026-10-04**. Active development is finished. A read-only Compute status
check found image `8.0`, one declared/live/ready instance. No webhook was disabled,
service deleted, credential revoked, or project paused during this handoff.
Running infrastructure can continue to incur charges.

For a new project or missing resources, use the [complete setup guide](setup.md).
For the original project, use the shorter path below. The [deployment history](deployment.md)
records the working configuration and known issues; [hackathon links](hackathon.md)
preserve the submission and demo.

## Resource inventory

| Resource | Original value |
| --- | --- |
| GitHub | `jobin212/supa-hack` (private at handoff) |
| Supabase project | `supa-hack-1` / `vgmkqcatnmmfhkvzsiwb` |
| Compute service | `threadsong`, public Dockerfile runtime, one 2 GB instance |
| Public base URL | `https://vgmkqcatnmmfhkvzsiwb.supabase.co/compute/v1/threadsong` |
| Jobs | `public.song_jobs` in hosted Postgres |
| Audio | Private Storage bucket `songs` |
| Ando workspace | StaterHQ / `3ebf20ba-0dad-4f8e-818f-77f0683e4332` |
| Songbot member | `b32f9500-b32c-4aa0-92f4-1ba3b6459d2e` |
| Demo channel | `#jobin-test` / `6414fbb5-6c96-44dd-a742-c863d40cc23d` |
| Ando outbound webhook | `wep_3172f4a9-0dd3-4573-bcb4-82b20a3e70c7` |
| Webhook URL | Public base URL + `/webhooks/ando` |
| Subscribed events | `message.created`, `webhook.test` |

IDs are configuration, not credentials. Check that these resources still exist
before reusing them; do not create a second receiver unnecessarily.

## What to preserve outside Git

These are backup tasks, **not completed backups**:

- Save the hosted configuration in an encrypted password manager or private
  backup. On the original Mac it is `.data/compute-live.env`; the fresh setup
  guide uses `.data/compute.env`. Both paths are gitignored. The original file
  existed with owner-only permissions at handoff.
- Preserve the Ando agent key and one-time webhook signing secret. The enrollment
  response is in `.data/ando/connection.json` on the original Mac, also owner-only
  and ignored. If the signing secret is lost, rotate/recreate the webhook and
  update both sides before enabling delivery.
- Keep access to the Supabase project, Ando workspace, and Google billing/API
  project. CLI login can be repeated; account sessions do not belong in Git.
- If preserving generated songs, export the job records **and the actual audio
  objects** with their object paths. The app stores audio in Storage, separately
  from Postgres; a database export alone is not an audio backup. Keep exports
  private: jobs can contain conversation context and sharing tokens.
- Archive the original [Loom video](hackathon.md) if needed. Slides are already
  versioned; the video and generated songs are not.

Local `.env` uses the SQLite demo configuration. It is **not** the hosted secret
file and must not be uploaded wholesale. Compute supplies `SUPABASE_DB_URL`,
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `PORT` in the verified deployment.

## Resume the original project

1. Clone the private repo with an authorized GitHub account. Install Python 3.12,
   uv, and a Supabase CLI with Compute support. The working CLI was `2.119.0`.
   Compute was an alpha feature; check current CLI help and product docs before
   changing infrastructure.

   ```sh
   git clone git@github.com:jobin212/supa-hack.git
   cd supa-hack
   uv sync --locked
   uv run pytest -q
   uv run ruff check src tests
   supabase login
   supabase link --project-ref vgmkqcatnmmfhkvzsiwb
   supabase compute status threadsong --project-ref vgmkqcatnmmfhkvzsiwb
   ```

2. If the service still exists, check readiness first:

   ```sh
   curl --fail-with-body https://vgmkqcatnmmfhkvzsiwb.supabase.co/compute/v1/threadsong/readyz
   ```

   Expect HTTP 200 and `{"ready":true}`. This verifies database/schema access,
   **not** Google or Ando credentials. If healthy and configuration is unchanged,
   no redeployment is required. `/health` previously returned a gateway 503 while
   `/readyz` and signed webhook tests worked; use the latter checks together.

3. If redeployment is needed, first disable song-event delivery in Ando and
   inspect outstanding jobs as described below. Restore the saved hosted secrets
   privately to `.data/compute.env`, set permissions to `600`, and review them
   against [.env.compute.example](../.env.compute.example). For an intact, previously
   configured webhook, keep `APP_MODE=live` and its matching signing secret.
   For a new webhook, follow the setup-mode sequence in [setup](setup.md).
   Check Google billing/model access and Ando agent/channel membership.

   ```sh
   chmod 600 .data/compute.env
   supabase secrets set --env-file .data/compute.env --project-ref vgmkqcatnmmfhkvzsiwb
   uv run python scripts/prepare_compute.py
   supabase compute push threadsong --project-ref vgmkqcatnmmfhkvzsiwb
   supabase compute status threadsong --project-ref vgmkqcatnmmfhkvzsiwb
   curl --fail-with-body https://vgmkqcatnmmfhkvzsiwb.supabase.co/compute/v1/threadsong/readyz
   ```

   Do not override the injected database URL with local SQLite settings. Always
   name `threadsong` in the push command. Do not drop/recreate the existing table
   or bucket; use the setup guide only for missing resources. If the project or
   service URL changed, update `PUBLIC_BASE_URL` and the Ando webhook endpoint.

4. Send Ando's signed `webhook.test`; expect HTTP 204. Enable `message.created`
   delivery, then make **one deliberate paid test** in `#jobin-test`:
   `@Songbot make a celebratory song about this thread`. Confirm progress updates,
   a reply in the original thread, and playable audio. Follow logs if needed:

   ```sh
   supabase compute logs threadsong --project-ref vgmkqcatnmmfhkvzsiwb --kind app --follow
   ```

For a free local refresher, use the README's [credential-free demo](../README.md#run-without-credentials).

## Optional operational shutdown

This sequence is a plan; it has **not** been performed:

1. Disable Ando song-event delivery first so new mentions cannot enqueue work.
2. Inspect jobs in the SQL Editor and let active work finish. Pending work can
   resume when the worker returns; decide what to do with it before redeploying.

   ```sql
   select id, status, stage, error_code, to_timestamp(updated_at) as updated_at
   from public.song_jobs
   where status not in ('complete', 'failed')
   order by updated_at;
   ```

3. Complete the private backups above. Then decide whether to retain Compute for
   playback or remove it to retire the service. The checked CLI exposes
   `compute delete` as irreversible; a pause/scale-to-zero workflow was not
   verified. Review current product controls before acting. Keep the Supabase
   project, Postgres, and Storage if existing songs should be recoverable.
4. Record any action and date here. Check billing for the retained resources;
   removing only the application does not remove the project or stored data.

**Sharing links require Compute.** `/s/<token>` looks up the job and creates a
short-lived Storage download URL. Removing Compute breaks those links while it
is absent, even if audio remains. Restoring the same URL, job records/tokens, and
Storage paths is necessary to restore the original links.

## Known limits when returning

- The configured Gemini and Lyria model names may change or lose access. Verify
  the models in `.env.compute.example` before a paid test; keep `uv.lock` for
  reproducible Python dependencies.
- Google content filtering is not fully predictable. New requests use a short
  summary of people/events plus optional style, not prewritten verses. See the
  [prompt lab notes](../scripts/LYRIA_PROMPT_LAB.md) for the earlier investigation.
- Rejected or uncertain paid music generations are not automatically repeated.
  Inspect the failed job before asking the bot again.
- Idle suspension/restart behavior during generation was not fully verified.
  Postgres leases preserve work but do not themselves wake a suspended service.
- This is the single-tenant Ando MVP. Slack integration and Notion enrichment
  were not implemented.
