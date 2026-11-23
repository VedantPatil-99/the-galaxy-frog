# 15. Preview and confirm Gemini notebook embeddings

Accepted 2026-11-22.

W3.7 adds Gemini Embedding 2 as an optional text embedding profile. The 1,536-dimensional
collection records the provider, model, asymmetric retrieval prompt version, and normalization.
The previous local and visual collection dimensions remain valid. The Gemini request batches
independent text inputs and uses separate retrieval prompts for documents and queries.
[Gemini embedding documentation](https://aistudio.google.com/docs/embeddings)

A preview computes the selected payload and estimated price locally. It records a short-lived
fingerprint of the operation, model profile, project, and exact prepared inputs. The receipt
lives inside the owning notebook and contains no raw passage or query. Gemini receives up to
16 passage excerpts of at most 2,000 UTF-8 bytes each, plus the retrieval prompt. A semantic
query sends its own text and retrieval prompt.

FastAPI requires explicit confirmation and commits the one-use receipt and the existing
project quota reservation before dispatch. A notebook lock serializes claims, and an active
receipt protects against overlapping requests. After an uncertain provider outcome the
receipt stays spent and its two-minute dispatch lease allows time to inspect project usage.
The HTTP adapter sends one request. Rebuilds activate the new collection only after vectors
are returned and persisted under the expected collection identity.

Pricing uses the verified standard paid text list rate of USD 0.20 per million input tokens.
The preview uses input bytes as a conservative token proxy and labels the result an estimate.
Actual charges and data use depend on the project's terms. Google lists product improvement
use on the free tier. [Gemini pricing and data use](https://ai.google.dev/gemini-api/docs/pricing#gemini-embedding-2)
