from __future__ import annotations

from anyio import to_thread
from litestar import Litestar
from litestar.config.cors import CORSConfig
from litestar.datastructures import State

from flashcards_generator.delivery.companion.auth import (
    TokenVerifier,
    verify_remote_token,
)
from flashcards_generator.delivery.companion.config import CompanionSettings
from flashcards_generator.delivery.companion.generation_jobs import (
    MAX_JOB_PARTS,
)
from flashcards_generator.delivery.companion.profiles import (
    NotebookLMProfiles,
)
from flashcards_generator.delivery.companion.routes import api_router
from flashcards_generator.delivery.composition import create_workflows
from flashcards_generator.services.local_generation_jobs import (
    LocalGenerationJobs,
)


async def stop_generation_jobs(app: Litestar) -> None:
    jobs: LocalGenerationJobs = app.state.jobs
    await to_thread.run_sync(jobs.close)


def create_app(
    settings: CompanionSettings,
    *,
    token_verifier: TokenVerifier = verify_remote_token,
) -> Litestar:
    profiles = NotebookLMProfiles(
        settings.data_dir,
        lambda home: create_workflows(notebooklm_home=home),
    )
    jobs = LocalGenerationJobs(profiles)
    return Litestar(
        route_handlers=[api_router],
        cors_config=CORSConfig(
            allow_origins=[settings.web_origin],
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
            allow_credentials=False,
        ),
        debug=False,
        multipart_form_part_limit=MAX_JOB_PARTS,
        on_shutdown=[stop_generation_jobs],
        state=State({
            "settings": settings,
            "profiles": profiles,
            "jobs": jobs,
            "token_verifier": token_verifier,
        }),
    )
