# 0017. Store private chats and revisioned prompt drafts

Accepted 2026-11-22.

W4.1 creates a chat table under the owning notebook. FastAPI verifies the notebook owner
before loading, creating, renaming, or saving a chat. Each lookup includes the notebook ID.
Row-level security is enabled with no direct browser policy. PostgreSQL is authoritative
and the browser uses the existing authenticated FastAPI proxy.

The chat list returns names and timestamps and uses an index starting with the notebook ID.
One chat detail loads its prompt. A create request has a client-generated chat ID so
retrying the same creation reopens it. A draft save compares the revision in a single
database update. Identical retries return the current revision. Different text from a
stale revision gets a conflict. The browser preserves the local edit during review and
uses the revision of the reviewed copy if the user confirms replacement.

The editor debounces saves by 350 ms and attempts to flush before navigation. The **Draft saved**
indicator reflects a server acknowledgement. Prompt contents are stored in PostgreSQL and
held in the current editor in memory. The URL stores only the chat and notebook IDs.

The existing selected-passage answer operation continues to validate citations before display.
W4.2 introduces durable answer history and generation runs using the chat identity.
