# 0016. Add Cohere Embed v4 to notebook semantic search

Accepted 2026-11-22.

Cohere Embed v4 uses the existing notebook index and one-use confirmation schema.
Its text-only adapter sends one bounded v2 Embed request with `search_document` for
passage excerpts and `search_query` for a query. It expects 1,536 finite float coordinates
per input and normalizes vectors before storage or comparison. The model and request
format have a separate collection revision. A switch from Gemini or local BGE-M3
requires an explicit rebuild and activates the new collection after the first batch persists.
[Cohere Embed API](https://docs.cohere.com/v2/reference/embed)

The preview binds the selected profile, exact text, collection state, credential scope,
and input rate. It uses the same notebook lock and dispatch lease as Gemini. Cohere
has a separate app quota shared across its notebooks, with five requests per minute,
64,000 estimated input tokens per minute, and 50 requests per Pacific calendar day.
The service sends at most 16 excerpts, each up to 2,000 UTF-8 bytes, or one query.
Source videos, document files, passage IDs, and evidence metadata stay in Galaxy Frog.

Trial mode is available for development evaluation. Production mode requires a positive
rate configured for the account because Cohere's public pricing page lists the newer
Embed generation. The application estimates tokens from UTF-8 input length locally.
Cohere meters actual token usage under the account's terms.
[Cohere pricing page](https://cohere.com/pricing) · [Cohere pricing guide](https://docs.cohere.com/docs/how-does-cohere-pricing-work)
