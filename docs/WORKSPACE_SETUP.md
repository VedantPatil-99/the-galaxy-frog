# Workspace setup and installation stages

Updated: 2026-11-19. Local foundations are implemented. A separate `galaxy-frog` Supabase project was created with explicit approval at a quoted $0/month; application configuration and live acceptance remain pending.

## Needed for the first packet

No new machine-level tool or application package is required for W1.1. The checkout already has Bun 1.4.0, Node 22.16.0, uv 0.12.7, Python 3.14.7, and the project's SQLAlchemy, asyncpg, Alembic, Pydantic, and test tooling. Use the existing PostgreSQL instance and environment; do not reinstall services or download models.

The root checkout's environment initially lacked dependencies already added by merged `main`. During this packet, `uv sync --directory backend --locked --inexact` installed the locked Google GenAI 3.5.0 SDK and its supporting dependencies, including httpx2, and rebuilt the editable application package. The lockfile was unchanged. `--inexact` preserved existing optional packages; no system tools or model weights were installed.

W1.1 adds only two tables: `workspace_accounts` and `workspace_notebooks`. It does not change or assign existing videos, transcripts, embeddings, or Study artifacts. New tables have PostgreSQL row-level security enabled and no browser access policies. W1.2 exposes notebook operations only through FastAPI's verified creator/ownership boundary; the private database connection still enforces the owner on every query.

From the repository root in **Git Bash**, apply the additive migration to the existing configured local development database:

```bash
uv run --no-sync --directory backend python -m alembic upgrade head
RUN_DATABASE_INTEGRATION=1 uv run --no-sync --directory backend python -m pytest tests/integration/test_workspace_ownership.py --no-cov
```

For the full backend coverage gate, use `RUN_DATABASE_INTEGRATION=1 uv run --no-sync --directory backend python -m pytest`. Omitting the PostgreSQL cases skips required coverage. Keep separately gated real-model/media tests disabled unless their prerequisites were explicitly provisioned.

`--no-sync` preserves the installed environment while running this packet. These commands do not install packages. Verify that `DATABASE_URL` points to the intended local development database before migrating it. Migration downgrade removes the new workspace tables and their contents; do not run it casually after storing workspace data.

## Supabase setup for the authentication and storage packets

A dedicated `galaxy-frog` project was created in Mumbai with explicit user approval after Supabase quoted $0/month. Project readiness is `ACTIVE_HEALTHY`. No hosted SQL, migrations, authentication-setting changes, or paid upgrades were authorized or performed. Keep application configuration and live sign-in/storage acceptance separate from project creation.

The setup needs the project URL, publishable key, a private FastAPI database connection, configured Google sign-in credentials, email-code delivery, allowed callback URLs, and private storage buckets. Keep actual values in ignored local environment files. Never put a service-role key, database password, or provider key in a `NEXT_PUBLIC_*` variable.

### W1.2 server configuration

The following settings are now consumed by FastAPI and documented in `.env.example`. Keep private mode disabled in the local environment until an actual project is configured. In production, private mode is mandatory even if the flag is false.

```bash
export WORKSPACE_AUTH_ENABLED=true
export SUPABASE_URL='https://your-project.supabase.co'
export SUPABASE_PUBLISHABLE_KEY='sb_publishable_your-project-key'
export WORKSPACE_CREATOR_EMAILS='["you@example.com"]'
export WORKSPACE_AUTH_TIMEOUT_SECONDS=5
```

Use the project's current publishable key, not a secret/service-role key or legacy anon JWT key. The server verifies each supplied user access token with Supabase Auth. Only confirmed, non-anonymous users whose verified emails are explicitly allowlisted can create/access notebooks. An empty allowlist denies all creators; it does not enroll the first user automatically. Allowlist edits take effect after restarting the API.

The new API provides `/v1/workspace/status`, `/session`, `/notebooks`, and `/notebooks/{notebook_id}` under the workspace prefix. All except status require `Authorization: Bearer <user-access-token>`. The existing Next.js proxy forwards this header to FastAPI and preserves the Bearer challenge. Notebook create/rename bodies contain only `title`; the server obtains owner IDs from verified identity.

Enabling private mode deliberately locks all existing video, transcript, frame, retrieval, Study, provider, and job routes, including for invited creators. These resources remain unavailable until W1.3 adds explicit ownership migration and resource authorization. The signed internal dispatch callback retains its separate authorization boundary. Health and safe workspace readiness are public. See [ADR 0010](adr/0010-private-workspace-auth.md).

No new runtime packages or migration are needed for W1.2 beyond W1.1's `20261119_0018`. The current packet tests the Auth adapter with synthetic responses and real PostgreSQL notebook isolation. Live provider acceptance and browser sign-in/cookie lifecycle remain pending; the sign-in UI is part of the workspace shell.

### W1.2 verification and remaining build check

The local backend/PostgreSQL gate passed 1,517 tests with four optional skips and 100% statement/branch coverage. Backend lint/format/types, generated-contract freshness, frontend lint/types, and all 52 frontend tests passed. The supported Webpack production build also passed:

```bash
bun run --cwd apps/web build --webpack
```

The default Turbopack build failed during loader-worker startup for the existing transcript component: the Node worker exited before it could connect. The tool's attempt to invoke a Git Bash shell also failed before running the command. No machine or security settings were changed, and the project still uses its original default build configuration. Confirm the normal build manually from Git Bash:

```bash
cd "/e/Projects/Galaxy Frog Root/galaxy-frog"
bun run build
```

Until that passes, the default-build gate remains open. Supabase read-only discovery and the separately approved $0 project creation completed; live Supabase verification is still pending application configuration. Design and approve a least-privilege hosted application database role separately from migration/admin access before hosted data is enabled.

Use Supabase's [server-side authentication guidance](https://supabase.com/docs/guides/auth/server-side), with cookie-based sessions and PKCE, when implementing Next.js sign-in/session forwarding. FastAPI remains responsible for verified identity and application ownership. Next.js does not query the application database. Live auth and storage checks stay pending until the project is configured; local tests must label simulated identity clearly.

## Application dependencies added when needed

Install dependencies in the packet that first uses them, with the project package manager and lockfile. Do not install this entire list in advance or add paid editor extensions.

- **Browser authentication:** `@supabase/ssr` and `@supabase/supabase-js` for Next.js session handling when the sign-in UI is built. The W1.2 server verification boundary uses the already installed HTTPX client; it does not need these frontend packages yet. Verify supported APIs against current official docs when integrating.
- **Remote and transient state:** `@tanstack/react-query` and `zustand` when the authenticated workspace shell first consumes the generated API contracts.
- **Shell:** existing shadcn Base UI components; add the Resizable/Command/Toast supporting packages only if the selected components need them. Doto uses `next/font/google`, without a separate npm or system font installation.
- **Answers and documents:** `react-markdown`, `remark-gfm`, math/KaTeX and Shiki support, followed by Tiptap's open-source editor packages when their renderers are implemented.
- **Maps, previews, and motion:** `@xyflow/react`, `pdfjs-dist`, and `motion` in the corresponding feature packets.
- **Document ingestion/export:** Python `pypdf` and `python-docx` when document adapters are introduced. External OCR providers and cloud storage remain explicit optional adapters.

Cloud Run, Vercel, Supabase email/storage, Tavily, and optional AWS configuration belong to their later packets. No cloud deployment, machine-level installation, model download, or automatic paid upgrade is needed to start the local foundation.
