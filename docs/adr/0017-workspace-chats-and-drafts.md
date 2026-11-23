# 0017. Store private chats and revisioned prompt drafts

Accepted 2026-11-22.

W4.1 creates a chat table under the owning notebook. FastAPI verifies the notebook owner
before loading, creating, renaming, or saving a chat. Each lookup includes the notebook ID.
Row-level security is enabled with no direct browser policy. PostgreSQL is authoritative
and the browser uses the existing authenticated FastAPI proxy.

The chat list returns names, timestamps, and whether each chat has draft text. It uses an
index starting with the notebook ID. One chat detail loads its prompt. The picker reflects
pending browser edits immediately and uses the chat URL for Back and Forward navigation.
A create request has a client-generated chat ID so
retrying the same creation reopens it. A draft save compares the revision in a single
database update. Identical retries return the current revision. Different text from a
stale revision gets a conflict. The browser preserves the local edit during review and
uses the revision of the reviewed copy if the user confirms replacement.

The editor records each unsynced edit in browser storage before the 350 ms server-save
debounce. Recovery keys include the verified account, notebook, and chat. Returning to
the same account in the same browser restores the remembered composer. The last recovery
copy remains on the device until the server acknowledges that exact edit or the user
chooses the saved version after a conflict. A save acknowledgement leaves newer edits
available for recovery. Quick questions use a notebook-specific browser draft.

The editor attempts a server save on blur, navigation, and when connectivity returns.
Sign-out allows up to 750 ms for the active save and then proceeds. A recovery copy may
remain in the browser profile after sign-out. The app opens it only inside the matching
authenticated workspace. Browser profile access or clearing site data affects these
copies. The URL stores only the chat and notebook IDs or the quick-question selection.

The existing selected-passage answer operation continues to validate citations before display.
W4.2 introduces durable answer history and generation runs using the chat identity.

Each verified browser session owns its mounted workspace, pending API requests and
draft acknowledgements. An account change aborts old requests, discards their responses,
and unmounts the previous workspace. The browser validates account ownership before
restoring chat or notebook navigation. Local recovery copies still use the verified
account, notebook and chat so the same owner can recover unsynced text after signing
back in. See [ADR 0010](0010-private-workspace-auth.md#w41-browser-account-changes).

## W4.2 first saved answer

A named chat can submit a saved draft with exactly one pasted-text source, version and
block. PostgreSQL serializes admissions per chat and enforces one active run. It stores
the ordered turn and queued run together with the model policy and a digest of the
validated passage, then clears only the accepted draft revision. An exact replay returns
the original turn and run. It never clears a later draft. A browser tab keeps an
unconfirmed submission briefly, scoped to the verified owner, notebook and chat.

The workspace worker freezes the exact excerpt and commits a possible-dispatch marker
before sending the question and excerpt to loopback Ollama. The worker uses an
admission grant capped at ten minutes and the verified access token's expiry. It checks
current worker Auth configuration, the creator allowlist, notebook ownership and evidence
before dispatch and publication. No bearer is persisted. The grant can outlive browser
sign-out until its expiry. Operators apply invitation changes to both API and worker.
[Supabase session semantics](https://supabase.com/docs/guides/auth/sessions)

The result stores only validated blocks and exact citation quotes. History withholds the
answer if the original version or passage is unavailable. Missing input, invalid output,
and uncertain dispatch are visible. An uncertain dispatch never runs again automatically.
The digest is observed at dispatch and checked again before publication. Existing quick
questions still use their established selected-passage path. Stop, the full withdrawal
lifecycle, durable video, and interactive Gemini have separate tickets.
