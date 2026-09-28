from __future__ import annotations

from litestar.datastructures import UploadFile
from litestar.exceptions import HTTPException, ServiceUnavailableException

from flashcards_generator.delivery.companion.generation_jobs import (
    CompanionJobRead,
    companion_job_read,
)
from flashcards_generator.delivery.companion.generation_uploads import (
    safe_filenames,
    save_uploads,
)
from flashcards_generator.services.dto.local_generation import (
    GenerationOptions,
)
from flashcards_generator.services.local_generation_jobs import (
    LocalGenerationJobs,
    LocalJobsClosedError,
    PendingJobLimitError,
    RetainedJobLimitError,
)
from flashcards_generator.services.local_generation_state import (
    GenerationJobSnapshot,
    LocalJobReservation,
)


async def create_local_job(
    jobs: LocalGenerationJobs,
    user_id: str,
    uploads: list[UploadFile],
    options: GenerationOptions,
) -> CompanionJobRead:
    filenames = safe_filenames(uploads)
    reservation = _reserve_job(jobs, user_id, filenames, options)
    submitted = False
    try:
        await save_uploads(reservation.input_dir, uploads, filenames)
        snapshot = _submit_job(jobs, reservation.id)
        submitted = True
        return companion_job_read(snapshot)
    finally:
        if not submitted:
            jobs.discard(reservation.id)


def _reserve_job(
    jobs: LocalGenerationJobs,
    user_id: str,
    filenames: list[str],
    options: GenerationOptions,
) -> LocalJobReservation:
    try:
        return jobs.reserve(user_id, filenames, options)
    except LocalJobsClosedError as error:
        raise ServiceUnavailableException(
            detail="O auxiliar local está encerrando. Tente novamente."
        ) from error
    except PendingJobLimitError as error:
        raise HTTPException(
            detail="Aguarde uma geração local terminar antes de enviar outra.",
            status_code=429,
        ) from error
    except RetainedJobLimitError as error:
        raise HTTPException(
            detail="Limite de gerações atingido.", status_code=429
        ) from error


def _submit_job(
    jobs: LocalGenerationJobs, job_id: str
) -> GenerationJobSnapshot:
    try:
        return jobs.submit(job_id)
    except LocalJobsClosedError as error:
        raise ServiceUnavailableException(
            detail="O auxiliar local está encerrando. Tente novamente."
        ) from error
