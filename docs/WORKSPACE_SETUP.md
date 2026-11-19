# Workspace setup and installation stages

Updated: 2026-11-19. The user has no Supabase project yet and requested local foundations and setup steps.

## Needed for the first packet

No new machine-level tool or application package is required for W1.1. The checkout already has Bun 1.4.0, Node 22.16.0, uv 0.12.7, Python 3.14.7, and the project's SQLAlchemy, asyncpg, Alembic, Pydantic, and test tooling. Use the existing PostgreSQL instance and environment; do not reinstall services or download models.

The root checkout's environment initially lacked dependencies already added by merged `main`. During this packet, `uv sync --directory backend --locked --inexact` installed the locked Google GenAI 3.5.0 SDK and its supporting dependencies, including httpx2, and rebuilt the editable application package. The lockfile was unchanged. `--inexact` preserved existing optional packages; no system tools or model weights were installed.

W1.1 adds only two tables: `workspace_accounts` and `workspace_notebooks`. It does not change or assign existing videos, transcripts, embeddings, or Study artifacts. New tables have PostgreSQL row-level security enabled and no browser access policies. FastAPI's eventual private connection must still enforce the verified owner on every query. The application does not yet expose these tables through HTTP.

From the repository root in **Git Bash**, apply the additive migration to the existing configured local development database:

```bash
uv run --no-sync --directory backend python -m alembic upgrade head
RUN_DATABASE_INTEGRATION=1 uv run --no-sync --directory backend python -m pytest tests/integration/test_workspace_ownership.py --no-cov
```

For the full backend coverage gate, use `RUN_DATABASE_INTEGRATION=1 uv run --no-sync --directory backend python -m pytest`. Omitting the PostgreSQL cases skips required coverage. Keep separately gated real-model/media tests disabled unless their prerequisites were explicitly provisioned.

`--no-sync` preserves the installed environment while running this packet. These commands do not install packages. Verify that `DATABASE_URL` points to the intended local development database before migrating it. Migration downgrade removes the new workspace tables and their contents; do not run it casually after storing workspace data.

## Supabase setup for the authentication and storage packets

Create a dedicated Galaxy Frog Supabase project before live hosted sign-in/storage acceptance. No project, account, paid service, or credential is created automatically by this packet.

The later setup needs the project URL, publishable key, a private FastAPI database connection, configured Google sign-in credentials, email-code delivery, allowed callback URLs, and private storage buckets. Keep actual values in ignored local environment files. Never put a service-role key, database password, or provider key in a `NEXT_PUBLIC_*` variable. The auth packet will add the exact consumed settings and examples when its implementation is ready.

Use Supabase's [server-side authentication guidance](https://supabase.com/docs/guides/auth/server-side), with cookie-based sessions and PKCE, when implementing Next.js sign-in/session forwarding. FastAPI remains responsible for verified identity and application ownership. Next.js does not query the application database. Live auth and storage checks stay pending until the project is configured; local tests must label simulated identity clearly.

## Application dependencies added when needed

Install dependencies in the packet that first uses them, with the project package manager and lockfile. Do not install this entire list in advance or add paid editor extensions.

- **Authentication:** `@supabase/ssr` and `@supabase/supabase-js` for Next.js session handling. Verify the supported APIs against current official docs when integrating.
- **Remote and transient state:** `@tanstack/react-query` and `zustand` when the authenticated workspace shell first consumes the generated API contracts.
- **Shell:** existing shadcn Base UI components; add the Resizable/Command/Toast supporting packages only if the selected components need them. Doto uses `next/font/google`, without a separate npm or system font installation.
- **Answers and documents:** `react-markdown`, `remark-gfm`, math/KaTeX and Shiki support, followed by Tiptap's open-source editor packages when their renderers are implemented.
- **Maps, previews, and motion:** `@xyflow/react`, `pdfjs-dist`, and `motion` in the corresponding feature packets.
- **Document ingestion/export:** Python `pypdf` and `python-docx` when document adapters are introduced. External OCR providers and cloud storage remain explicit optional adapters.

Cloud Run, Vercel, Supabase email/storage, Tavily, and optional AWS configuration belong to their later packets. No cloud deployment, machine-level installation, model download, or automatic paid upgrade is needed to start the local foundation.
