"""Opt-in hosted Gemini acceptance; show synthetic answers only when explicitly requested."""

import argparse
import asyncio
import json
from time import perf_counter
from uuid import uuid4

from galaxy_frog.adapters.generation.gemini import GeminiGeneration, GeminiJSON
from galaxy_frog.adapters.generation.study_gemini import GeminiStudyGenerator
from galaxy_frog.application.provider_policy import ProviderFailure
from galaxy_frog.application.study_generation import StudyTimeline, generate_study
from galaxy_frog.config import Settings
from galaxy_frog.domain.providers import GEMINI_TEXT_MODELS
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.study import StudyCitation, StudyKind, StudySource
from galaxy_frog.domain.transcripts.models import RetrievalUnit
from galaxy_frog.domain.videos.models import VideoSourceKind


async def progress(_done: int, _total: int, _warnings: tuple[str, ...]) -> None:
    pass


async def run(
    model: str,
    live: bool,
    *,
    show_output: bool = False,
    roles: tuple[str, ...] = ("generation", "study"),
) -> int:
    settings = Settings()
    failed = False
    text = "In this synthetic test, the frog's badge number is 42."
    for role in roles:
        key, project = settings.gemini_credentials(role)
        report: dict[str, object] = {"role": role, "model": model, "data": "synthetic text only"}
        if not key or not project:
            report["status"] = "setup_required"
            failed = True
        elif not live:
            report["status"] = "credentials_present_not_live_tested"
        else:
            provider = GeminiJSON(
                key.get_secret_value(), model, max_output_tokens=1024, timeout_seconds=45
            )
            started = perf_counter()
            try:
                video_id = uuid4()
                if role == "generation":
                    unit = RetrievalUnit("a" * 64, 0, 10_000, text, ("b" * 64,))
                    draft = await GeminiGeneration(provider).generate(
                        "What is the frog's badge number?",
                        (RetrievedEvidence(video_id, unit, 1.0),),
                    )
                    if (
                        "42" not in draft.answer
                        or not draft.citations
                        or any(
                            citation.retrieval_unit_id != unit.unit_id or citation.quote not in text
                            for citation in draft.citations
                        )
                    ):
                        raise ValueError("Synthetic answer or citation check failed.")
                    if show_output:
                        report["output"] = {
                            "answer": draft.answer,
                            "confidence": draft.confidence.value,
                            "citations": [
                                {"retrieval_unit_id": item.retrieval_unit_id, "quote": item.quote}
                                for item in draft.citations
                            ],
                        }
                else:
                    timeline = StudyTimeline(
                        StudySource(
                            video_id=video_id,
                            kind=VideoSourceKind.YOUTUBE,
                            external_id="abcdefghijk",
                            canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
                            title="Synthetic provider smoke; no video was accessed",
                            duration_ms=10_000,
                        ),
                        (
                            StudyCitation(
                                source_id="cue:test",
                                modality="transcript",
                                start_ms=0,
                                end_ms=10_000,
                                seek_ms=0,
                                quote=text,
                                timestamp_url=None,
                                cue_id="b" * 64,
                                unit_ids=("a" * 64,),
                            ),
                        ),
                        (),
                        (),
                    )
                    generated = await generate_study(
                        StudyKind.CHAPTERS,
                        timeline,
                        None,
                        GeminiStudyGenerator(provider),
                        progress,
                    )
                    if not generated.document.chapters[0].citations:
                        raise ValueError("Synthetic Study citation check failed.")
                    if show_output:
                        report["output"] = {
                            "chapters": [
                                chapter.model_dump(mode="json")
                                for chapter in generated.document.chapters
                            ]
                        }
                report.update(
                    status="hosted_accepted",
                    returned_model=provider.observed_model,
                    usage=provider.usage,
                    processing_ms=round((perf_counter() - started) * 1000),
                )
            except ProviderFailure as error:
                report.update(status="hosted_rejected", code=error.code.value)
                failed = True
            except Exception as error:
                report.update(status="check_failed", error_type=type(error).__name__)
                failed = True
        print(json.dumps(report), flush=True)
    return int(failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=sorted(GEMINI_TEXT_MODELS), required=True)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Authorize two real Google API calls with synthetic text; project billing terms apply.",
    )
    parser.add_argument(
        "--show-output",
        action="store_true",
        help="Show validated synthetic answers; never prints environment values or provider errors.",
    )
    parser.add_argument("--role", choices=("generation", "study", "both"), default="both")
    args = parser.parse_args()
    selected_roles = ("generation", "study") if args.role == "both" else (args.role,)
    if not args.live:
        configuration = Settings()
        for selected_role in selected_roles:
            selected_key, selected_project = configuration.gemini_credentials(selected_role)
            print(
                json.dumps(
                    {
                        "role": selected_role,
                        "model": args.model,
                        "status": "credentials_present_not_live_tested"
                        if selected_key and selected_project
                        else "setup_required",
                    }
                )
            )
    else:
        raise SystemExit(
            asyncio.run(run(args.model, True, show_output=args.show_output, roles=selected_roles))
        )
