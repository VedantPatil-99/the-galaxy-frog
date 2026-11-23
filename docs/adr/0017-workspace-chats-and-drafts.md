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
