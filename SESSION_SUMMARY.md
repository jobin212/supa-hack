# Supabase setup and Compute session summary

Date: 2026-10-03

Historical setup notes from before the app was deployed. For current installation
instructions, use [docs/setup.md](docs/setup.md); for verified deployment history,
use [docs/deployment.md](docs/deployment.md).

## User intent

Explore the new Supabase Compute private alpha, verify access, and prepare this
project locally. User prefers the experimental Docker-free local stack.

## Project and environment

- Workspace: `/Users/jobin/supa-hack`
- Account: `jobin212@gmail.com` (verified with `supabase whoami`)
- Hosted project: `supa-hack-1`
- Project reference: `vgmkqcatnmmfhkvzsiwb`
- Region: `us-west-2`
- Project was `ACTIVE_HEALTHY` when checked.
- Mac uses Apple Silicon (arm64).
- Supabase CLI 2.119.0 installed at `/Users/jobin/.local/bin/supabase` using the official release archive; SHA-256 checked against the official Homebrew formula.
- CLI browser login completed and the workspace was linked to the hosted project.
- Codex's Supabase connector is also connected; its authentication is separate from CLI login.

## Completed setup and verification

- Ran `supabase init` to create `supabase/config.toml`.
- Enabled Compute and selected Docker-free local development:

```toml
[experimental]
compute = true
stack = true
```

- `compute = true` exposed the experimental Compute CLI commands.
- `supabase compute list --project-ref vgmkqcatnmmfhkvzsiwb` succeeded.
- After linking, `supabase compute list` also succeeded without an explicit project reference.
- This confirms the remote Compute listing API is accessible; deployment permissions have not been tested.
- Created a local Node starter with:

```sh
supabase compute new hello-compute --runtime node --size 2gb --exposure private
```

- Starter source: `/Users/jobin/supa-hack/supabase/compute/hello-compute/index.mjs`
- Configuration: `[compute.hello-compute]`, Node runtime, 2 GB, private exposure, default one instance if deployed.
- Starter exports a fetch handler returning JSON with the request path and optional `GREETING` environment variable.
- The listing reported `hello-compute` as local and **not deployed**.
- Added `.gitignore` for environment files, dependencies, and macOS metadata. Supabase's generated ignore file excludes `.temp` and `.branches`.
- No hosted deployment, schema change, or migration was performed. No changes were committed.

## How Compute works

The new Compute product runs application services, background workers, and agent
workloads alongside a Supabase project's database. It is distinct from the older
database "Compute size" billing/settings feature.

Official descriptions cover Node, Deno, and Dockerfile runtimes, long-running
services in Linux environments, public HTTP services, and private workers or
sandboxes. Runtime, size, exposure, and source location live in `config.toml`.

- `compute new`: creates local starter files; does not deploy or establish alpha access.
- `compute push`: builds and deploys the selected service remotely.
- `compute list`: lists local definitions and remotely deployed services.
- `compute status`: reports deployment/build details.
- `compute logs`: reads application, request, and build logs; supports `--follow`.

The current starter is private, so deployment would not create a public HTTP URL.
Compute is still a private alpha; exact behavior and commands can change.

## Docker-free local stack

The user chose Docker-free local development. `stack = true` has been added under
`[experimental]` in the existing config. The local stack has **not been started or
verified**. No Docker-compatible runtime was found during initial setup.

Correction to an earlier session statement: Docker is not the only local option.
Supabase's October 2026 announcement documents an experimental native-process
stack, enabled by this setting or `SUPABASE_EXPERIMENTAL_STACK=1`.

The local database/services and hosted Compute are separate. Accessing or deploying
hosted Compute does not require starting the local Supabase database.

## Useful next commands

Run from `/Users/jobin/supa-hack`:

```sh
# Start and inspect the experimental local stack (not yet run)
supabase start
supabase status

# Recheck hosted Compute
supabase compute list

# Read version-specific CLI documentation
supabase compute new --help
supabase compute push --help
supabase compute logs --help

# Future deployment and inspection (deployment has NOT been run)
supabase compute push hello-compute
supabase compute status hello-compute
supabase compute logs hello-compute --follow

# Stop local services when finished
supabase stop
```

## Documentation found

- [Compute product overview](https://supabase.com/compute)
- [Technical introduction and Docker-free local stack announcement](https://supabase.com/blog/select-2026-build-anything)
- [Supabase Select 2026 recap](https://supabase.com/blog/supabase-select-2026-recap)
- [General CLI setup](https://supabase.com/docs/guides/local-development/cli/getting-started)

A dedicated public getting-started guide for the new Compute runtime was not found
in the searches performed. The installed CLI's `--help` provides concrete command
details. The user's Supabase contact may have additional private-alpha documentation.

## Remaining work

1. Start and verify the Docker-free local stack; troubleshoot any alpha limitations.
2. Decide what actual application or worker to build and whether it needs a public endpoint.
3. Deploy only when ready; deployment capability and application behavior remain unverified.

Do not copy access tokens or login verification codes into this summary or source control.
