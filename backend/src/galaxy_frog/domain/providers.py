"""Provider identities and versioned routing policy."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class Capability(StrEnum):
    TRANSCRIPTION = "transcription"
    TEXT_EMBEDDING = "text_embedding"
    VISUAL_EMBEDDING = "visual_embedding"
    RERANKING = "reranking"
    OCR = "ocr"
    VISION = "vision"
    GENERATION = "generation"
    STUDY = "study"


class Location(StrEnum):
    LOCAL = "local"
    FREE_CLOUD = "free_cloud"
    AWS = "aws"


class ProviderMode(StrEnum):
    LOCAL_ONLY = "automatic_local_only"
    CLOUD_PERMITTED = "automatic_cloud_permitted"
    MANUAL = "manual"


type ProviderId = Annotated[str, StringConstraints(min_length=1, max_length=240)]
type ProfileId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,39}$")]


class ProfileRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: ProfileId
    version: int = Field(ge=1)


class ProviderProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: ProviderMode
    candidates: dict[Capability, tuple[ProviderId, ...]]
    evaluation: bool = False
    pinned_revisions: dict[ProviderId, str] | None = None
    embedding_collections: dict[Capability, UUID] | None = None

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        for chain in self.candidates.values():
            if not 1 <= len(chain) <= 4 or len(set(chain)) != len(chain):
                raise ValueError("Provider chains require one to four distinct candidates.")
            if self.evaluation and len(chain) != 1:
                raise ValueError("Evaluation requires a single provider per capability.")
        if self.evaluation and (
            self.mode is not ProviderMode.MANUAL
            or set(self.candidates) != set(Capability) - {Capability.OCR}
        ):
            raise ValueError(
                "Evaluation requires exact pins for all executable local capabilities."
            )
        return self


class ProviderProfileVersion(ProfileRef):
    policy: ProviderProfile
    created_at: datetime | None = None


class ProviderProfileEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    policy: ProviderProfile


class ProviderOperation(StrEnum):
    IMPORT = "import"
    SEARCH = "search"
    QUESTION = "question"
    MOMENT = "moment"
    STUDY = "study"


class ExternalData(StrEnum):
    AUDIO = "audio"
    FRAMES = "frames"
    FRAMES_AND_TEXT = "frames_and_text"
    PAID_TEXT = "paid_text"


class ConsentScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: ProviderId
    data: ExternalData


class ProviderSelection(BaseModel):
    """Profile and optional one-operation grant supplied by the client."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: ProfileRef
    consent_id: UUID | None = None


class ProviderConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: ProfileRef
    operation: ProviderOperation
    source: str = Field(min_length=1, max_length=255)
    scopes: tuple[ConsentScope, ...] = Field(min_length=1, max_length=4)


class ProviderConsent(ProviderConsentRequest):
    consent_id: UUID
    created_at: datetime
    revoked_at: datetime | None
    run_id: UUID | None
    dispatch_count: int
