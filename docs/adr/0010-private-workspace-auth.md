# ADR 0010 — Private workspace authentication and legacy isolation

Date: 2026-11-19

## Status

Accepted for W1.2. Live Supabase email-session acceptance through the local FastAPI application
passed on 2026-11-20. Browser sign-in/cookies, Google OAuth, private storage, hosted deployment,
CI, and merge remain separate acceptance gates.

## Context

The existing local application has videos, transcripts, jobs, provider grants, and Study artifacts
without account ownership. W1.1 introduced account/notebook roots without assigning legacy data.
Exposing private notebooks must not also expose those unowned resources to signed-in users.
At the time of this decision, no Supabase project was configured. Local tests must distinguish
simulated Auth from live identity-provider acceptance. The separately approved project and local
connection subsequently passed live email-session acceptance on 2026-11-20.

## Decision

- FastAPI verifies each workspace bearer token by calling the configured Supabase Auth
  `GET /auth/v1/user` authority. It never trusts a client-supplied owner ID, decoded JWT payload,
  user metadata, or the presence of a publishable key alone as proof of identity.
- The adapter uses the existing injected HTTPX client, HTTPS-only origins, a short configured
  network timeout, no redirect following, no automatic retries, and a bounded streamed response.
  Tokens and upstream response bodies are not persisted or included in public errors.
- Use Supabase's current `sb_publishable_` key on the `apikey` header and the user's access token
  on `Authorization`. Reject secret/service-role keys and legacy key formats in this setting.
  No Supabase administration credential is needed for request verification.
- Only confirmed, non-anonymous users in the server's explicit verified-email creator allowlist
  can reach notebook persistence. Empty allowlists grant no access. Compare normalized email
  values from the Auth response; keep the stable Supabase subject UUID as the account ID.
- `WORKSPACE_AUTH_ENABLED=true` enables private mode locally. `APP_ENV=production` always uses
  private mode even if that flag is false. Missing auth configuration fails closed; health and
  non-sensitive workspace readiness remain public.
- Wrap all existing video, retrieval, frame, Study, provider, and job routes in one legacy guard.
  In private mode they return `LEGACY_RESOURCES_LOCKED`, including for invited creators. W1.3
  must implement explicit ownership/migration and resource authorization before those paths
  can be enabled for account use. Registering an account never transfers old data.
- Preserve the internal QStash callback's separate signature boundary. Never expose it through
  the browser proxy. A user bearer token is not a dispatch signature.
- New `/v1/workspace` endpoints provide readiness, verified session information, and notebook
  create/list/read/rename operations. Every notebook operation derives ownership from verified
  identity. Missing and inaccessible notebooks have the same public 404 outcome.
- Use stable 401/403/503 error envelopes, no-store private/error responses, and the Bearer
  challenge for workspace authentication failures. Forward that challenge through the existing
  Next.js proxy. Generate frontend contracts from FastAPI OpenAPI.

## Consequences

Local development keeps its existing application behavior when private mode is disabled. This is
an intentional compatibility change for production: old unowned API routes become unavailable,
and private routes require Supabase configuration and an explicit creator allowlist. Do not deploy
this packet as though ownership migration and a complete hosted workspace were already ready.

Auth verification adds a network request per private API request and fails visibly during Auth
outages. Allowlist changes require a server restart; Supabase owns access-token expiry and session
behavior. Do not promise instant JWT invalidation on logout. Later sharing and grant revocation
must enforce their own application authorization. Browser sign-in, cookie/session lifecycle, and
the new UI are implemented with the workspace shell; this packet exposes the verified API boundary.

No new runtime package, cloud project, paid resource, system service, or model installation is
needed for this packet. Existing RLS-without-browser-policies remains a second boundary; FastAPI's
private database connection still requires explicit ownership filtering on every query.

References: [Supabase Auth user endpoint](https://github.com/supabase/auth#readme),
[Supabase API key roles](https://supabase.com/docs/guides/api/api-keys).
