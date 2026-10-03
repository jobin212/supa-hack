# Deployment status

Updated 2026-10-03. Supabase project: `vgmkqcatnmmfhkvzsiwb` (`supa-hack-1`).

Handoff check on 2026-10-04: Compute still reported image `8.0` and one
declared/live/ready instance. No shutdown was performed. See the
[retirement and restart runbook](retirement.md) for resuming or retiring it.
The application checks and generation results below are the 2026-10-03 history.

- Compute service: `threadsong`, public Dockerfile runtime, one 2 GB instance,
  image version `8.0`.
- Base URL: `https://vgmkqcatnmmfhkvzsiwb.supabase.co/compute/v1/threadsong`
- Ando webhook: base URL + `/webhooks/ando`.
- Agent: Songbot in StaterHQ; joined `#jobin-test` and verified read/post access.
- Channel allowlist: `6414fbb5-6c96-44dd-a742-c863d40cc23d`.
- Webhook ID: `wep_3172f4a9-0dd3-4573-bcb4-82b20a3e70c7`.
- Current subscription: `message.created` and `webhook.test`.
- Ando's official setup test completed successfully at 20:47 UTC; live-mode
  delivery was verified again after enabling message events.
- Signing secret is stored in gitignored `.env` and Supabase project secrets.
- The job migration is applied to the hosted database; the `songs` bucket is private.

The deployment runs in `APP_MODE=live`. `/readyz` returned 200 after querying
the hosted job table; see the later gateway caveat for `/health`. The worker is enabled,
with a 30-second target song duration. The first deployment used setup mode to
verify signatures before enabling song requests.

Project secrets reach the Compute runtime through the launcher. The built-in
project variables include `SUPABASE_DB_URL`, `SUPABASE_URL`, and
`SUPABASE_SERVICE_ROLE_KEY`; the app can use these without uploading reserved
`SUPABASE_` names. Live database readiness confirms this works; no manually
copied database password was required for the hosted service. Local development
still uses the SQLite demo defaults unless explicitly configured otherwise.

The first real song completed at 21:17:12 UTC on 2026-10-03. The original
user mention was delivered correctly but initially ignored because image `2.0`
did not recognize Ando's native mention format. Image `3.0` recognizes
`<!workspace_membership:MEMBER_ID|Display Name>` by membership ID. The ignored
job was requeued before any paid generation had occurred.

Verified job `af6c427f-dd63-4cc7-8b04-79b19406af30` through thread context,
Gemini lyrics, Lyria audio, private Storage, and the final Ando thread reply:

- Context: 1.57 seconds; draft: 5.58 seconds; generation and upload: 19.39
  seconds; publish: 1.16 seconds. About 28 seconds overall after requeue.
- Song: "Eco-Friendly Victory Lap". The 30-second prompt produced a 61.41-second
  MP3, 44.1 kHz stereo at 192 kbps; duration is model guidance, not an exact limit.
- Both acknowledgment and result appear in the original thread
  `67b33c7a-c4d0-4235-9f3c-c91a2c62d6e2` in `#jobin-test`.
- The sharing route returned 307, followed by HTTP 200 and 1,480,009 audio bytes
  from the signed private Storage URL. macOS `afinfo` validated the MP3 format.
- Hosted database configuration has `SUPABASE_DB_URL` and no `DATABASE_URL`
  override. Local `.env` remains SQLite demo mode and is not deployed.

The worker logs stage starts, elapsed times, and terminal status without message
content or secrets. Follow them with `supabase compute logs threadsong
--project-ref vgmkqcatnmmfhkvzsiwb --kind app --follow` (one command).

Two later hyperpop requests reached lyrics generation but failed during the
Lyria call in under a second. A controlled replay of the latest saved draft
returned HTTP 400; inspection of the same request confirmed Google's
`prohibited_content` response (input blocked by its content filter). Google
did not identify the triggering words. The original deployment had discarded
the HTTP details, so the exact original responses cannot be reconstructed.
No audio was returned, and the latest job remains failed as `generation_blocked`.

Image `5.0` distinguishes content filtering, rate/quota limits, request/access
errors, transport failures, and timeouts. It logs only allowlisted provider
metadata and returns a specific Ando explanation for known failures. It does
not automatically retry rejected or uncertain music requests. Regression
checks: 42 passed, one optional local Postgres integration check skipped.
The latest diagnostic failure message in Ando was corrected to explain the filter.

Image `6.0` improves the moderation message: it says the generated song prompt
was blocked, does not attribute the rejection specifically to lyrics, and
suggests a new mention with a different style or mood. The existing diagnostic
reply in the sales-loss thread was updated and verified through Ando's API.
Ten workflow checks passed. Generation prompting is unchanged; the summary-first
experiment in `scripts/LYRIA_PROMPT_LAB.md` is local only.

Runtime secrets were validated before activation; redeploy after changing them
so the app process reloads its environment. One complete background run has been
verified; prolonged suspension and restart recovery remain separate scenarios.

Image `7.0` adds one evolving status reply per job: thread read, writing lyrics,
generating (with title and style), uploading, and ready or failed. The status
message ID is checkpointed in the existing job payload. Creation uses a stable
idempotency key; updates use Ando's message PATCH API. Each status request has a
five-second limit and is best-effort. Status failures do not retry generation.
The final song or failure notification remains a separate thread reply.

Verification: 46 tests passed, including restart/message reuse, failed status
creation and edits, and exactly one generation despite status errors; the
optional local Postgres integration test was skipped. A live Songbot message
was created and edited in the demo thread, and its replacement text was fetched
back successfully. This progress-only test did not generate a song.

Image `8.0` replaces Gemini-written lyrics with a one- or two-sentence summary
of the people, events, and intended mood. Only an explicitly requested style is
added. Lyria receives that summary and the duration hint and writes lyrics and
music itself. The progress reply now says the thread is being summarized. Old
checkpointed lyric drafts remain readable, and generation retry rules are unchanged.

Verification: 48 tests passed and one optional local Postgres integration test
was skipped; Ruff passed. A live local run of the same composer against the real
sales-win thread returned a valid 66.35-second MP3 in 29.44 seconds. Exact private
requests/results are in `.data/simple-summary-test/20261003T221112Z/`. The test
did not post to Ando. Compute reports image `8.0` active with one ready instance.
After deployment, `/readyz` returned 200 and a signed `webhook.test` returned
204 without enqueueing a song. `/health` returned a gateway 503 even while those
application endpoints succeeded; use readiness and webhook checks for this deployment.
This successful example does not establish a lower moderation rejection rate.

Deploy code changes with:

```sh
uv run python scripts/prepare_compute.py
supabase compute push threadsong --project-ref vgmkqcatnmmfhkvzsiwb
```

The build directory is generated and gitignored. Do not copy `.env`, `.data`,
API responses, or test music into it. Private enrollment and webhook records
remain under `.data/ando/` for local setup recovery.
