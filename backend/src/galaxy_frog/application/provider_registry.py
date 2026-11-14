"""Safe provider catalog and deterministic built-in profile chains."""

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, computed_field

from galaxy_frog.adapters.vision.siglip import SIGLIP_MODEL, SIGLIP_REVISION
from galaxy_frog.config import Settings
from galaxy_frog.domain.providers import Capability, Location, ProviderMode, ProviderProfile
from galaxy_frog.domain.retrieval.reranking import BGE_RERANKER_MODEL, BGE_RERANKER_REVISION

CLOUDFLARE_MODEL = "@cf/openai/whisper-large-v3-turbo"
BEDROCK_MODEL = "amazon.nova-lite-v1:0"


class PricingSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: str
    billing_unit: str
    currency: str | None = "USD"
    tier_note: str
    verified_at: date = date(2026, 11, 14)

    @computed_field
    @property
    def stale(self) -> bool:
        return datetime.now(UTC).date() - self.verified_at > timedelta(days=30)


async def probe_ollama_models(base_url: str) -> dict[str, str]:
    import httpx

    if not local_ollama_origin(base_url):
        return {}
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            async with client.stream("GET", f"{base_url.rstrip('/')}/api/tags") as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 262_144:
                        return {}
                import json

                response_data = json.loads(payload)
            return {
                str(item["model"]): str(item["digest"])
                for item in response_data["models"]
                if not item.get("remote_model") and not item.get("remote_host")
            }
    except httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError:
        return {}


class ProviderEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    capability: Capability
    location: Location
    provider: str
    model: str
    revision: str | None = None
    device: str | None = None
    readiness: Literal["configured", "disabled", "setup_required", "catalog_only"]
    language_coverage: str | None = None
    code_switching: Literal["verified", "unverified"] | None = None
    timestamp_granularity: str | None = None
    confidence: str | None = None
    diarization: bool | None = None
    hardware: str | None = None
    privacy: str
    summary: str
    local_execution_evidence: str | None = None
    measured_speed: str | None = None
    pricing: PricingSource | None = None
    capability_url: str | None = None


def local_ollama_origin(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
    )


def secret_present(secret: SecretStr | None) -> bool:
    return secret is not None and bool(secret.get_secret_value().strip())


def provider_catalog(settings: Settings) -> tuple[ProviderEntry, ...]:
    local = "configured" if local_ollama_origin(settings.ollama_base_url) else "setup_required"
    return (
        ProviderEntry(
            provider_id="faster-whisper",
            capability=Capability.TRANSCRIPTION,
            location=Location.LOCAL,
            provider="faster-whisper",
            model=settings.asr_model,
            revision=settings.asr_model_revision,
            device=settings.asr_device,
            readiness="configured",
            language_coverage="Multilingual models",
            code_switching="unverified",
            timestamp_granularity="Segment intervals in milliseconds",
            confidence="Language probability and mean word confidence",
            diarization=False,
            hardware=f"Provisioned {settings.asr_device} runtime ({settings.asr_compute_type})",
            privacy="Audio stays on this machine.",
            summary="Default for imports with unavailable or unusable captions.",
            local_execution_evidence="Phase 2 exit gate used the multilingual small model on CUDA.",
            measured_speed="31.72 s for an 11 s speech fixture on RTX 2050 (Phase 2 probe).",
            capability_url="https://github.com/SYSTRAN/faster-whisper",
        ),
        ProviderEntry(
            provider_id="ollama-embedding",
            capability=Capability.TEXT_EMBEDDING,
            location=Location.LOCAL,
            provider="ollama",
            model=settings.embedding_model,
            revision=settings.embedding_model_revision,
            readiness=local,
            privacy="Transcript and query text stay in local Ollama.",
            summary="Normalized 1,024-dimensional text vectors for the matching collection.",
            language_coverage="BGE-M3 documents coverage for more than 100 languages.",
            capability_url="https://huggingface.co/BAAI/bge-m3",
        ),
        ProviderEntry(
            provider_id="siglip",
            capability=Capability.VISUAL_EMBEDDING,
            location=Location.LOCAL,
            provider="siglip",
            model=SIGLIP_MODEL,
            revision=SIGLIP_REVISION,
            device=settings.visual_device,
            readiness="configured" if settings.visual_embedding_enabled else "disabled",
            hardware=f"Provisioned offline model and {settings.visual_device} runtime",
            privacy="Frames and queries stay on this machine.",
            summary="Normalized 768-dimensional visual vectors for the matching collection.",
            local_execution_evidence="Phase 5 verified offline CUDA indexing and frame retrieval.",
            measured_speed="6 to 29 s visual queries on RTX 2050 (Phase 5 closed-pool runs).",
            capability_url="https://huggingface.co/google/siglip-base-patch16-224",
        ),
        ProviderEntry(
            provider_id="bge-reranker",
            capability=Capability.RERANKING,
            location=Location.LOCAL,
            provider="bge",
            model=BGE_RERANKER_MODEL,
            revision=BGE_RERANKER_REVISION,
            device="cuda",
            readiness="configured",
            hardware="Provisioned CUDA FP16 runtime and offline model",
            privacy="Retrieved text stays on this machine.",
            summary="Reranks the bounded fused transcript candidates.",
            local_execution_evidence="Phase 3 verified the local CUDA reranker.",
            measured_speed="44.2 s for 30 candidates on RTX 2050 (first Phase 3 run).",
            capability_url="https://huggingface.co/BAAI/bge-reranker-v2-m3",
        ),
        ProviderEntry(
            provider_id="ollama-vision",
            capability=Capability.VISION,
            location=Location.LOCAL,
            provider="ollama",
            model="qwen3-vl:2b-instruct",
            revision=settings.qwen_visual_revision,
            readiness=local if settings.vlm_provider != "disabled" else "disabled",
            hardware="Provisioned Ollama with the pinned Qwen model and GPU residency",
            privacy="Selected frames and text stay in local Ollama.",
            summary="Selective reasoning over a verified frame.",
            local_execution_evidence="Phase 6 verified local frame grounding and citation seek.",
            capability_url="https://ollama.com/library/qwen3-vl",
        ),
        *(
            ProviderEntry(
                provider_id=provider_id,
                capability=capability,
                location=Location.LOCAL,
                provider="ollama",
                model=settings.generation_model,
                revision=settings.generation_model_revision,
                readiness=local,
                hardware="Provisioned Ollama model",
                privacy="Selected text stays in local Ollama.",
                summary=summary,
                capability_url="https://docs.ollama.com/api/generate",
            )
            for provider_id, capability, summary in (
                (
                    "ollama-text",
                    Capability.GENERATION,
                    "Grounded questions and moment explanations.",
                ),
                (
                    "ollama-study",
                    Capability.STUDY,
                    "Versioned study materials with validated citations.",
                ),
            )
        ),
        ProviderEntry(
            provider_id="gemini-vision",
            capability=Capability.VISION,
            location=Location.FREE_CLOUD,
            provider="google-gemini",
            model=settings.gemini_visual_model or "Operator-selected Gemini model",
            revision=settings.gemini_visual_revision,
            readiness=(
                "configured"
                if settings.vlm_provider != "disabled"
                and settings.external_frame_processing_enabled
                and secret_present(settings.gemini_api_key)
                and settings.gemini_visual_model
                and settings.gemini_visual_revision
                else "setup_required"
            ),
            privacy="Sends selected frames and text to Google. Free-tier data terms apply.",
            summary="Consented vision reasoning with revision and citation validation.",
            pricing=PricingSource(
                source_url="https://aistudio.google.com/docs/pricing",
                billing_unit="Input and output tokens",
                tier_note="Eligibility, rates and data terms depend on model and project tier.",
            ),
            capability_url="https://aistudio.google.com/docs/image-understanding",
        ),
        ProviderEntry(
            provider_id="llamaparse-ocr",
            capability=Capability.OCR,
            location=Location.FREE_CLOUD,
            provider="llamaparse",
            model="LlamaParse OCR",
            readiness=(
                "configured"
                if settings.external_frame_processing_enabled
                and settings.ocr_external_frames_permitted
                and secret_present(settings.llama_cloud_api_key)
                else "setup_required"
            ),
            privacy="Sends selected frames to LlamaIndex.",
            summary="OCR for unique frames during a consented import.",
            pricing=PricingSource(
                source_url="https://developers.llamaindex.ai/llamaparse/general/pricing/",
                billing_unit="Credits per page",
                tier_note="Credits depend on the parsing version, mode and account plan.",
            ),
            capability_url="https://developers.llamaindex.ai/llamaparse/",
        ),
        ProviderEntry(
            provider_id="cloudflare-whisper",
            capability=Capability.TRANSCRIPTION,
            location=Location.FREE_CLOUD,
            provider="cloudflare",
            model=CLOUDFLARE_MODEL,
            readiness=(
                "configured"
                if settings.cloudflare_asr_enabled
                and settings.cloudflare_account_id
                and secret_present(settings.cloudflare_api_token)
                else "setup_required"
            ),
            language_coverage="Multilingual Whisper ASR",
            code_switching="unverified",
            timestamp_granularity="Provider segment times converted to milliseconds",
            confidence="Detected language probability where supplied",
            diarization=False,
            privacy="Sends bounded normalized audio to Cloudflare Workers AI.",
            summary="Opt-in fallback for captionless imports within the cloud audio limit.",
            pricing=PricingSource(
                source_url="https://developers.cloudflare.com/workers-ai/platform/pricing/",
                billing_unit="Audio minutes and Workers AI neurons",
                tier_note="Daily free allocation is shared. Paid accounts can incur overage.",
            ),
            capability_url="https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/",
        ),
        ProviderEntry(
            provider_id="bedrock-nova-lite",
            capability=Capability.GENERATION,
            location=Location.AWS,
            provider="amazon-bedrock",
            model=BEDROCK_MODEL,
            revision="v1:0",
            readiness=(
                "configured"
                if settings.bedrock_text_enabled
                and secret_present(settings.aws_bearer_token_bedrock)
                else "setup_required"
            ),
            privacy=f"Sends selected text to the configured AWS Region ({settings.bedrock_region}).",
            summary="Explicit paid choice for grounded text inference.",
            pricing=PricingSource(
                source_url="https://aws.amazon.com/bedrock/pricing/",
                billing_unit="Input and output tokens",
                tier_note="Standard on-demand pricing depends on model and Region.",
            ),
            capability_url="https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-lite.html",
        ),
        ProviderEntry(
            provider_id="amazon-transcribe",
            capability=Capability.TRANSCRIPTION,
            location=Location.AWS,
            provider="amazon-transcribe",
            model="Amazon Transcribe batch",
            readiness="catalog_only",
            timestamp_granularity="Provider word timestamps",
            confidence="Provider word confidence",
            diarization=True,
            privacy="Batch transcription requires staging media in S3.",
            summary="Documented AWS transcription capability.",
            pricing=PricingSource(
                source_url="https://aws.amazon.com/transcribe/pricing/",
                billing_unit="Audio duration",
                tier_note="Rates vary by Region and monthly volume.",
            ),
            capability_url="https://docs.aws.amazon.com/transcribe/latest/APIReference/API_StartTranscriptionJob.html",
        ),
    )


def builtin_profiles() -> dict[str, ProviderProfile]:
    local = {
        Capability.TRANSCRIPTION: ("faster-whisper",),
        Capability.TEXT_EMBEDDING: ("ollama-embedding",),
        Capability.VISUAL_EMBEDDING: ("siglip",),
        Capability.RERANKING: ("bge-reranker",),
        Capability.VISION: ("ollama-vision",),
        Capability.GENERATION: ("ollama-text",),
        Capability.STUDY: ("ollama-study",),
    }
    free_cloud = {
        **local,
        Capability.TRANSCRIPTION: ("faster-whisper", "cloudflare-whisper"),
        Capability.OCR: ("llamaparse-ocr",),
        Capability.VISION: ("ollama-vision", "gemini-vision"),
    }
    return {
        "local": ProviderProfile(mode=ProviderMode.LOCAL_ONLY, candidates=local),
        "free_cloud": ProviderProfile(mode=ProviderMode.CLOUD_PERMITTED, candidates=free_cloud),
        "economy": ProviderProfile(mode=ProviderMode.CLOUD_PERMITTED, candidates=free_cloud),
        "aws": ProviderProfile(
            mode=ProviderMode.MANUAL,
            candidates={**local, Capability.GENERATION: ("bedrock-nova-lite",)},
        ),
        "quality": ProviderProfile(
            mode=ProviderMode.CLOUD_PERMITTED,
            candidates={**free_cloud, Capability.VISION: ("gemini-vision", "ollama-vision")},
        ),
        "evaluation": ProviderProfile(mode=ProviderMode.MANUAL, candidates=local, evaluation=True),
    }


def profile_validation_error(
    policy: ProviderProfile, catalog: tuple[ProviderEntry, ...]
) -> str | None:
    entries = {item.provider_id: item for item in catalog}
    for capability, chain in policy.candidates.items():
        for provider_id in chain:
            entry = entries.get(provider_id)
            if (
                entry is None
                or entry.readiness == "catalog_only"
                or entry.capability is not capability
            ):
                return "Each candidate must support its selected capability."
            if (
                policy.mode is ProviderMode.LOCAL_ONLY and entry.location is not Location.LOCAL
            ) or (policy.mode is ProviderMode.CLOUD_PERMITTED and entry.location is Location.AWS):
                return "The provider is outside the selected mode."
            if policy.evaluation and entry.location is not Location.LOCAL:
                return "Evaluation requires local providers."
    if policy.evaluation:
        selected = {item for chain in policy.candidates.values() for item in chain}
        if set(policy.pinned_revisions or {}) != selected or set(
            policy.embedding_collections or {}
        ) != {
            Capability.TEXT_EMBEDDING,
            Capability.VISUAL_EMBEDDING,
        }:
            return "Evaluation requires model revisions and both embedding collection IDs."
    return None
