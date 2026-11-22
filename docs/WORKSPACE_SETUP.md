# Workspace setup and installation stages

Updated 2026-11-22. Live Supabase email sign-in and private preview Storage through FastAPI passed. The user also confirmed the W2 browser email callback, session after refresh, and sign-out.

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

A dedicated `galaxy-frog` project was created in Mumbai with explicit user approval after Supabase quoted $0/month. Project readiness is `ACTIVE_HEALTHY`. The user subsequently approved connecting its URL and modern publishable key in the ignored root `.env`, allowlisting their creator email, and sending a test login email. One retry, read-only Auth log inspection, and a further fresh-link test after pausing Raycast previews were approved in the conversation. Three email requests were accepted in total; the last completed application verification. No hosted database SQL, migrations, authentication-setting changes, or paid upgrades were authorized or performed. The existing local database remains the application database.

The setup needs the project URL, publishable key, a private FastAPI database connection, configured Google sign-in credentials, email-code delivery, allowed callback URLs, and private storage buckets. Keep actual values in ignored local environment files. Never put a service-role key, database password, or provider key in a `NEXT_PUBLIC_*` variable.

### W1.2 server configuration

The following settings are now consumed by FastAPI and documented in `.env.example`. The live smoke helper enables private mode only in its own process; the ordinary local environment has not been switched to private mode. In production, private mode is mandatory even if the flag is false.

```bash
export WORKSPACE_AUTH_ENABLED=true
export SUPABASE_URL='https://your-project.supabase.co'
export SUPABASE_PUBLISHABLE_KEY='sb_publishable_your-project-key'
export WORKSPACE_CREATOR_EMAILS='["you@example.com"]'
export WORKSPACE_AUTH_TIMEOUT_SECONDS=5
```

Use the project's current publishable key, not a secret/service-role key or legacy anon JWT key. The server verifies each supplied user access token with Supabase Auth. Only confirmed, non-anonymous users whose verified emails are explicitly allowlisted can create/access notebooks. An empty allowlist denies all creators; it does not enroll the first user automatically. Allowlist edits take effect after restarting the API.

The new API provides `/v1/workspace/status`, `/session`, `/notebooks`, and `/notebooks/{notebook_id}` under the workspace prefix. All except status require `Authorization: Bearer <user-access-token>`. The existing Next.js proxy forwards this header to FastAPI and preserves the Bearer challenge. Notebook create/rename bodies contain only `title`; the server obtains owner IDs from verified identity.

Enabling private mode locks the old global video, transcript, frame, retrieval, Study, provider, and job routes. W1.3 provides separate notebook-scoped source, transcript, and frame reads for explicitly assigned videos. The signed internal dispatch callback retains its separate authorization boundary. Health and safe workspace readiness are public. See [ADR 0010](adr/0010-private-workspace-auth.md) and [ADR 0011](adr/0011-private-legacy-source-reads.md).

No new runtime packages or migration are needed for W1.2 beyond W1.1's `20261119_0018`. The packet tests the Auth adapter with synthetic responses and real PostgreSQL notebook isolation, and now has a successful live Supabase email-session check. Browser sign-in/cookie lifecycle remains pending; the sign-in UI is part of the workspace shell.

### W1.2 verification

The final local backend/PostgreSQL gate passed **1,536 tests with four optional skips and 100% statement/branch coverage** after local Auth configuration, including 19 helper tests. Backend/helper lint, format, and strict types passed. W1.2's previously verified generated-contract freshness, frontend lint/types, all 52 frontend tests, and both production build modes remain valid: the live-check packet changes no frontend or HTTP schemas.

```bash
bun run build
bun run --cwd apps/web build --webpack
```

The agent execution environment initially failed during Turbopack loader-worker startup. On 2026-11-19, the user's PowerShell terminal was directly inspected: the normal `bun run build` compiled successfully, completed TypeScript, generated all 10 static pages, finalized optimization, and returned to the prompt. The default-build gate is closed without changing project configuration or machine/security settings.

Live application acceptance passed at **2026-11-20 16:52:35 IST (11:22:35 UTC)**. The helper exchanged a fresh email link, verified the creator through FastAPI against Supabase Auth, successfully accessed private notebooks, confirmed anonymous requests return 401 and legacy-resource requests return 403, confirmed private responses use `Cache-Control: no-store`, and received a successful logout response for the test session. Its ignored receipt contains only status, timestamp, and boolean check results. These checks used the existing localhost application database; they do not demonstrate a hosted application deployment, browser cookie lifecycle, Google OAuth, or private storage.

Earlier replay attempts returned `otp_expired`; the approved read-only Auth logs showed that the previous link had already completed a login four seconds before the helper exchanged it. The next test passed after the user paused Raycast link previews. This is consistent with prefetching as the cause, but does not prove which process opened the earlier link. No SMTP or template change was needed for the successful check. Design and approve a least-privilege hosted application database role separately from migration/admin access before hosted data is enabled.

### Local live Auth check

`scripts/workspace_auth_smoke.py` uses the configured creator email, Supabase URL/publishable key, and the existing localhost database. It requires the W1.1 workspace tables before sending an email. It never runs hosted SQL or changes Auth settings. No additional package or machine installation is needed.

The approved live check is complete. For a future explicitly authorized test, from the repository root in **Git Bash**, verify the email requested for that test:

```bash
./backend/.venv/Scripts/python.exe scripts/workspace_auth_smoke.py --verify --email 'your-allowlisted-email@example.com'
```

On Windows, this opens a password-style paste field that supports Ctrl+V. Use an unopened email confirmation link, a numeric code if the email contains one, or the local browser address obtained after opening the link. Keep these credentials out of chat, command arguments, and shell history. `--terminal-input` retains the hidden terminal prompt as an explicit fallback.

The existing email template sends a link by default, so a numeric code is not expected. If the link was already opened, verification can use the issued session in the returned `http://localhost:3000/#access_token=…` address instead of replaying the one-time link. Other session origins are rejected. The token remains untrusted until the existing FastAPI verifier checks it with the configured Supabase Auth authority. Expired or already-used links without a usable returned session require approval for a fresh email; the helper never resends automatically. See [Supabase passwordless email behavior](https://supabase.com/docs/guides/auth/auth-email-passwordless).

#### Numeric code alternative for link previews

The user reported Raycast previews copied links. A preview opening a one-time Auth link could consume it before verification; Supabase documents prefetching as a cause of invalid/expired link errors. A fresh-link test passed after previews were paused, without configuring another provider. The numeric-code alternatives below remain optional future delivery setup, not a prerequisite for the completed W1.2 check.

**Template availability correction:** Since 2026-06-03, new Free projects using Supabase's built-in SMTP cannot edit Auth email templates. This project was created after that date, so the template-only instructions previously suggested are insufficient. Free projects can customize templates after configuring their own SMTP provider; a Supabase Pro upgrade is not required for that route. See the [specific breaking change](https://supabase.com/changelog/46599-changes-to-email-template-customisation-on-free-tier).

For a manual setup using numeric codes:

1. Obtain an email provider's SMTP host, port, username, password, and an authorized sender address. Provider signup, sender/domain verification, and any provider charges are separate from Supabase.
2. Configure these in the `galaxy-frog` project's [SMTP Settings](https://supabase.com/dashboard/project/uuzjwtdpkmbzxslpojrb/auth/smtp), keeping credentials private. No SMTP provider has been configured by the agent.
3. Open [Email Templates](https://supabase.com/dashboard/project/uuzjwtdpkmbzxslpojrb/auth/templates). Select **Magic Link** (or **Magic link / OTP**). Set the subject to `Your Galaxy Frog sign-in code` and replace its body with:

   ```html
   <h2>Your Galaxy Frog sign-in code</h2>
   <p>Enter this code in Galaxy Frog:</p>
   <p><strong>{{ .Token }}</strong></p>
   ```

4. Save the template. To use codes for first-time accounts too, apply the same body to **Confirm signup**. The current test account has already confirmed its email.
5. Explicitly approve one new test email after saving. Enter its numeric code in the existing local verification window; the helper already supports email OTPs. Template changes do not replace previously sent emails.

The user also identified the [Send Email Hook](https://supabase.com/docs/guides/auth/auth-hooks/send-email-hook). It is [available on Free and Pro](https://supabase.com/docs/guides/auth/auth-hooks) and replaces built-in email delivery with a custom provider. It requires a deployed handler, webhook signature verification, and private provider/hook credentials; it is an alternative to custom SMTP, not a template-only switch. The guide shows an Edge Function with Resend. No hook implementation, deployment, or hosted configuration change is authorized merely by discussing this option.

For a further test using the existing built-in service, temporarily pausing Raycast's link previews before handling a newly approved link avoids adding an email provider. Whether it resolves this instance still needs live verification. The built-in provider allows two emails per hour, so another request may need to wait for its allowance after the two approved sends. Custom SMTP and hooks have their own limits and provider requirements. Do not increase limits or configure paid email services automatically. See [email prefetching and OTP templates](https://supabase.com/docs/guides/auth/auth-email-templates#email-prefetching) and [Auth rate limits](https://supabase.com/docs/guides/auth/rate-limits).

The check verifies the creator identity, notebook access, anonymous denial, the legacy-resource lock, and `Cache-Control: no-store`, then attempts to log out the test session. It stores only status/time/boolean results in ignored `tmp/workspace-auth/verification.json`. This receipt blocks another email request through `--send-code` by default. Only after separate explicit approval for one further email may `--fresh-email-approved` be added; it archives the previous receipt before one new dispatch, without retries. Never remove the receipt to bypass authorization. The helper has 19 focused tests covering origin restrictions, the separate resend approval, one-attempt dispatch, used-link handling, and credential redaction.

Use Supabase's [server-side authentication guidance](https://supabase.com/docs/guides/auth/server-side), with cookie-based sessions and PKCE, when implementing Next.js sign-in/session forwarding. The smoke helper's handling of the default email redirect is only a local acceptance aid. FastAPI remains responsible for verified identity and application ownership. Next.js does not query the application database. Browser sign-in and storage acceptance belong to their later packets; local tests must label simulated identity clearly.

### W1.3 legacy video assignments and private previews

Apply the additive W1.3 schema to the local application database with
`uv run --no-sync --directory backend python -m alembic upgrade head`. Migration
`20261120_0019` creates an empty ownership table. Use the account ID and notebook ID of an
existing creator notebook and the original video ID for each source selected for that notebook.
The authenticated session and notebook responses include the account and notebook IDs.
The application's local PostgreSQL `videos` table contains the source IDs and titles.

From the repository root in Git Bash, preview the assignment:

```bash
uv run --no-sync --directory backend python -m galaxy_frog.entrypoints.workspace_assign_legacy \
  --owner-id "$CREATOR_ID" --notebook-id "$NOTEBOOK_ID" --video-id "$VIDEO_ID"
```

Set the shell variables to the selected UUIDs first. Repeat `--video-id` to specify up to
100 distinct videos. After checking the selection, add `--apply` to that command to commit it.
The command accepts a localhost database only, applies the batch atomically, and rejects an
attempt to move a video that already belongs to another notebook. An identical repeat
reports zero new assignments. Applied assignments can be read under
`/v1/workspace/notebooks/{notebook_id}/sources`, with transcript and frame routes scoped by
video ID. Private mode must be enabled. Test with two verified accounts before hosted use.

Local previews use the existing `VISION_WORKSPACE_ROOT`. To activate the optional hosted reader:

1. Configure a Supabase bucket named `workspace-previews` as **private** and ensure Storage
   has no policies granting client roles access to this bucket. Limit uploads to JPEGs up to 8 MiB.
2. Stage an approved JPEG under `<video UUID>/<frame UUID>.jpg` using the matching frame record.
   Use a synthetic JPEG fixture for the first live check.
3. Store a separate modern secret key in FastAPI's private environment as
   `SUPABASE_STORAGE_SECRET_KEY`, and set `WORKSPACE_PREVIEW_STORAGE=supabase`. The existing
   `SUPABASE_URL` selects the project. The secret key is elevated and belongs only to this server.
4. Verify the owning creator can fetch the preview through FastAPI. Check that anonymous
   requests receive 401, another creator receives 404, and a publishable key alone cannot
   fetch the object directly from Storage. Inspect bucket-specific and schema-wide Storage
   policies for direct browser read grants. Confirm the served response uses
   `Cache-Control: no-store`.

The user provisioned the private bucket and configured the server key. The 2026-11-20
live check used a disposable JPEG against real Supabase Storage, local PostgreSQL, and
two synthetic FastAPI identities. It confirmed the owner receives the exact bytes with
`no-store`, the other identity gets 404, and anonymous FastAPI callers get 401. Direct
anonymous and publishable-key-only Storage requests were denied. The test removed its
object and rolled back its local records. A separate read verified that a missing hosted
preview is reported as 404 even when Supabase wraps the missing-object response in
HTTP 400. On 2026-11-21 the Storage dashboard showed zero bucket policies, zero additional
`storage.objects` policies, and zero `storage.buckets` policies. This confirmed the private
bucket has no direct read grant for ordinary signed-in users. The live test used the hosted
Storage reader only for the test process.

The local adapter requires no additional dependencies. See [private buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals),
[Storage access control](https://supabase.com/docs/guides/storage/security/access-control), and
[Supabase secret keys](https://supabase.com/docs/guides/getting-started/api-keys).

## W2 browser sign-in

Local mode needs no account or new machine installation. Use the normal `bun run dev` command from the repository root. The web package now includes `@supabase/ssr` and `@supabase/supabase-js`. On a fresh checkout, `bun install --frozen-lockfile` installs the locked dependencies.

For a separate private-mode browser check, configure W1.2 on FastAPI and put these server settings in `apps/web/.env.local`. Use the same project for both processes.

```dotenv
WORKSPACE_WEB_ORIGIN=http://localhost:3000
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_your-project-key
```

Add the exact `http://localhost:3000/auth/callback` under Supabase **Authentication → URL Configuration → Redirect URLs**, then restart the web process. Open Galaxy Frog at the configured origin, request an email link with an existing allowlisted account, and open the link in the same browser profile. Verify the notebook list across a refresh, sign out, and confirm the workspace returns to sign-in. Use the deployed HTTPS origin and its matching callback URL if testing a hosted instance. [Supabase redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls)

## W3.1 private text sources

Apply the additive Alembic migration to the intended local database with `bun run db:migrate`.
In a private notebook, open **Text sources → Paste text** and choose plain text or Markdown.
Pastes are limited to 65,536 characters, 128 KiB of UTF-8 text, and 2,048 blocks.
The paste flow uses the existing database and web dependencies.

## W3.2 document file imports

The backend dependencies now include `pypdf`, `python-docx`, and `pypdfium2`. The
local migration and dependency install use the existing tools:

```bash
uv sync --directory backend --locked --inexact
bun run db:migrate
```

In a private notebook, open **Documents → Import a document**. Choose a PDF, DOCX, TXT,
or Markdown file of up to 8 MiB. PDFs support up to 80 pages and have a page-by-page image
preview. Open **Extracted text** to read the text from that page. Pages without extractable
text are marked as candidates for OCR. DOCX previews include main-body paragraphs and table
cells. TXT and Markdown files use UTF-8 and open as text.

The original is stored in the backend's private `WORKSPACE_DOCUMENT_ROOT` directory, which
defaults to `tmp/workspace-documents` in the repository. A backup of document sources includes
both the database and this directory. The file path stays server-side, and only a
verified notebook owner can request its extracted text or generated page images.

## W3.3 scanned PDF page OCR

This packet uses the installed PDF renderer and HTTP client. Run `bun run db:migrate`
against the intended local database to add the page OCR records. FastAPI leaves document
OCR off by default. To enable it, set `WORKSPACE_DOCUMENT_OCR_ENABLED=true` in the backend
environment alongside the existing `LLAMA_CLOUD_API_KEY` and restart FastAPI.

In a private notebook, open an imported PDF to a page with no extractable text. Open
**Read this page with OCR**, approve sending that page image to LlamaParse, and select **Run OCR**. Sending
the page can use LlamaCloud credits. The owner can reopen the same page to see the saved
result or processing status. Each source version and page can be submitted once, including
when a connection failure leaves the outcome unknown.
[LlamaParse pricing](https://developers.llamaindex.ai/llamaparse/general/pricing/)
[LlamaParse data retention](https://developers.llamaindex.ai/llamaparse/general/data_retention/)

## W3.4 notebook keyword search

Run `bun run db:migrate` to index saved text, document extractions, and page OCR in the
intended local database. The existing video transcript index is reused. In a private
notebook, enter search words under **Find words in sources**. Select any match to open its
source preview. The small duration beside the results measures the browser request.
Search uses the local database and existing dependencies. [Measurement log](WORKSPACE_MEASUREMENTS.md)

## W3.5 answers from selected passages

Start the installed Ollama service. FastAPI uses the existing `OLLAMA_BASE_URL` and
`GENERATION_MODEL` settings and checks the installed model against `GENERATION_MODEL_REVISION`
when a revision is configured. This packet uses the current database schema.

In a private notebook, use **Find words in sources**, check up to six matching passages,
then open **Ask about selected passages**. Submit the question with **Ask with Ollama**.
Select a citation to reopen its source. The badge beside the answer measures the browser
request. Questions that need temporal resolution require the video-specific workflow.
[Measurement log](WORKSPACE_MEASUREMENTS.md)

## W3.6 local semantic search

Run `bun run db:migrate` against the intended local database. Start the installed Ollama
service with BGE-M3 available through the existing `OLLAMA_BASE_URL` and `EMBEDDING_MODEL`
settings. The app checks `EMBEDDING_MODEL_REVISION` when a digest is pinned there.

In a private notebook, open **Search by meaning**. Choose **Build local index**, then use
**Index next batch** until the coverage count includes all passages you want to search.
Enter a topic under **Topic or phrase** and open a result to inspect the source evidence.
If the installed model revision changes, **Rebuild for this revision** explicitly replaces
the notebook's derived semantic vectors after a batch succeeds. [Measurement log](WORKSPACE_MEASUREMENTS.md)

## W3.7 Gemini semantic search

Apply `bun run db:migrate` against the intended local database. To enable the optional
profile, set `WORKSPACE_GEMINI_EMBEDDING_ENABLED=true` in the backend environment and
configure `GEMINI_EMBEDDING_API_KEY` with its matching `GEMINI_EMBEDDING_PROJECT_ID`.
FastAPI applies the existing Gemini project limits to confirmed requests. The API key stays
on the server. Gemini's standard paid input rate and free-tier data usage are documented at
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-embedding-2).

In **Search by meaning**, select **Gemini Embedding 2 · Cloud**. Choose the Gemini indexing
or rebuild preview, read the batch and remaining-index estimates, then confirm the text
transfer for that batch. A semantic search also displays the query and estimate before its
own confirmation. A confirmed receipt can dispatch once. Review Gemini project usage if the
outcome is uncertain. [Measurement log](WORKSPACE_MEASUREMENTS.md)

## W3.8 Cohere semantic search

Cohere uses the W3.7 schema and existing Python HTTP client. To try the optional profile,
set these variables in the backend environment and restart FastAPI.

```dotenv
WORKSPACE_COHERE_EMBEDDING_ENABLED=true
COHERE_EMBEDDING_API_KEY=<your Cohere trial key>
COHERE_EMBEDDING_MODE=trial
```

Use trial mode for development evaluation. It displays the trial estimate and is unavailable
when `APP_ENV=production`. For paid use, supply a Cohere production key, set
`COHERE_EMBEDDING_MODE=production`, and set `COHERE_EMBEDDING_USD_PER_MILLION_TOKENS`
to the positive USD rate your account pays per million Embed v4 input tokens. FastAPI keeps
the key server-side and uses input length to estimate usage. Actual billing comes from Cohere.
[Cohere pricing guide](https://docs.cohere.com/docs/how-does-cohere-pricing-work)

In **Search by meaning**, select **Cohere Embed v4 · Cloud**. Review the excerpts count and
estimate under **Preview Cohere indexing** or **Preview Cohere rebuild**. Confirm each batch
to update the index. **Preview Cohere search** shows the exact query for a separate confirmation.
If a call has an uncertain outcome, inspect Cohere usage before approving another request.
[Cohere data use](https://cohere.com/enterprise-data-commitments) · [Measurement log](WORKSPACE_MEASUREMENTS.md)

## W4.1 Private chats and prompt drafts

Apply `bun run db:migrate` to the configured local database and restart FastAPI. The
migration adds the private chat table using the existing dependencies.

Open an owned notebook and select **New chat**. Use **Rename** to change its name. Edits
are kept in the current browser while the server saves them. The same account can return
to the last composer after refreshing, closing the tab, or signing out. **Draft saved**
means the server has confirmed the chat draft, so it can be opened on another device.
If a newer revision is saved, use **Review saved draft** to choose which text to keep.
The pencil beside a chat name signals text in its composer. Use the picker or browser
Back and Forward to switch between chats and their drafts.

**Quick question** drafts stay in the current browser profile and site address. Clearing
site data removes browser-only drafts and unsynced edits.

To use the draft with the existing local Ollama workflow, find words in the notebook
and select supporting passages before choosing **Ask with Ollama**. The answer is a
session result in this packet. Saved answer history and recoverable runs follow in W4.2.
[Measurement log](WORKSPACE_MEASUREMENTS.md)

## Application dependencies added when needed

Install dependencies in the packet that first uses them, with the project package manager and lockfile. Do not install this entire list in advance or add paid editor extensions.

- **Remote and transient state:** consider `@tanstack/react-query` and `zustand` when shared caching or durable chat state requires them.
- **Shell:** existing shadcn Base UI components; add Resizable/Command/Toast packages only when the relevant controls need them. Doto and Commissioner use `next/font/local` with bundled WOFF2 files and OFL notices.
- **Answers and documents:** `react-markdown`, `remark-gfm`, math/KaTeX and Shiki support, followed by Tiptap's open-source editor packages when their renderers are implemented.
- **Maps, previews, and motion:** `@xyflow/react`, `pdfjs-dist`, and `motion` in the corresponding feature packets.
- **Document ingestion/export:** Python `pypdf` and `python-docx` when document adapters are introduced. External OCR providers and cloud storage remain explicit optional adapters.

Cloud Run, Vercel, Supabase email/storage, Tavily, and optional AWS configuration belong to their later packets. No cloud deployment, machine-level installation, model download, or automatic paid upgrade is needed to start the local foundation.
