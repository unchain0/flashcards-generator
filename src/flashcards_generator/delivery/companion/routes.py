from __future__ import annotations

from anyio import to_thread
from litestar import Request, Router, get, post
from litestar.datastructures import FormMultiDict, State, UploadFile
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    NotFoundException,
)
from litestar.params import FromPath
from litestar.response import File
from pydantic import ValidationError

from flashcards_generator.delivery.companion.auth import authorized_user
from flashcards_generator.delivery.companion.generation import (
    create_local_job,
)
from flashcards_generator.delivery.companion.generation_jobs import (
    MAX_JOB_PARTS,
    MAX_JOB_REQUEST_BYTES,
    CompanionJobRead,
    companion_job_read,
)
from flashcards_generator.delivery.companion.profiles import (
    NotebookLMProfiles,
)
from flashcards_generator.delivery.web.schemas import NotebookLMStatusRead
from flashcards_generator.services.dto.local_generation import (
    GenerationOptions,
)
from flashcards_generator.services.dto.workflow import AuthStatus
from flashcards_generator.services.local_generation_jobs import (
    LocalGenerationJobs,
)


@get("/notebooklm/status")
async def notebooklm_status(
    request: Request, state: State
) -> NotebookLMStatusRead:
    user_id = await authorized_user(request)
    profiles: NotebookLMProfiles = state.profiles
    status = await to_thread.run_sync(profiles.for_user(user_id).auth_status)
    return _status_read(status)


@post("/notebooklm/login", status_code=200)
async def notebooklm_login(
    request: Request, state: State
) -> NotebookLMStatusRead:
    user_id = await authorized_user(request)
    profiles: NotebookLMProfiles = state.profiles
    status = await to_thread.run_sync(profiles.for_user(user_id).login)
    return _status_read(status)


@post(
    "/jobs",
    status_code=202,
    request_max_body_size=MAX_JOB_REQUEST_BYTES,
    multipart_form_part_limit=MAX_JOB_PARTS,
)
async def create_job(request: Request, state: State) -> CompanionJobRead:
    user_id = await authorized_user(request)
    profiles: NotebookLMProfiles = state.profiles
    workflow = profiles.for_user(user_id)
    status = await to_thread.run_sync(workflow.auth_status)
    if not status.authenticated:
        raise HTTPException(
            detail="Conecte sua conta do NotebookLM antes de gerar flashcards.",
            status_code=409,
        )
    form = await request.form()
    try:
        uploads = _form_uploads(form)
        options = _generation_options(form)
        jobs: LocalGenerationJobs = state.jobs
        return await create_local_job(jobs, user_id, uploads, options)
    finally:
        await form.close()


@get("/jobs/{job_id:str}")
async def get_job(
    request: Request,
    state: State,
    job_id: FromPath[str],
) -> CompanionJobRead:
    user_id = await authorized_user(request)
    jobs: LocalGenerationJobs = state.jobs
    job = jobs.get(user_id, job_id)
    if job is None:
        raise NotFoundException(detail="Geração não encontrada")
    return companion_job_read(job)


@get("/jobs/{job_id:str}/artifacts/{artifact_path:path}")
async def download_artifact(
    request: Request,
    state: State,
    job_id: FromPath[str],
    artifact_path: FromPath[str],
) -> File:
    user_id = await authorized_user(request)
    jobs: LocalGenerationJobs = state.jobs
    path = jobs.artifact(user_id, job_id, artifact_path.lstrip("/"))
    if path is None:
        raise NotFoundException(detail="Arquivo não encontrado")
    return File(path, filename=path.name, media_type="text/csv")


def _form_uploads(form: FormMultiDict) -> list[UploadFile]:
    if "files" not in form:
        return []
    uploads: list[UploadFile] = []
    for value in form.getall("files"):
        if not isinstance(value, UploadFile):
            raise HTTPException(
                detail="O envio de arquivos é inválido.", status_code=400
            )
        uploads.append(value)
    return uploads


def _generation_options(form: FormMultiDict) -> GenerationOptions:
    allowed = {
        "files",
        "language",
        "difficulty",
        "quantity",
        "timeout",
        "instructions",
    }
    if not set(form.keys()) <= allowed:
        raise HTTPException(
            detail="Há campos de geração desconhecidos.", status_code=400
        )
    values = {
        field: _form_text(form, field, default)
        for field, default in (
            ("language", "pt_BR"),
            ("difficulty", "medium"),
            ("quantity", "standard"),
            ("timeout", "900"),
            ("instructions", ""),
        )
    }
    try:
        return GenerationOptions.model_validate(values)
    except ValidationError as error:
        raise HTTPException(
            detail="As configurações de geração são inválidas.",
            status_code=400,
        ) from error


def _form_text(form: FormMultiDict, field: str, default: str) -> str:
    if field not in form:
        return default
    values = form.getall(field)
    if len(values) != 1 or not isinstance(values[0], str):
        raise HTTPException(
            detail="O formulário de geração é inválido.", status_code=400
        )
    return values[0]


def _status_read(status: AuthStatus) -> NotebookLMStatusRead:
    return NotebookLMStatusRead(
        authenticated=status.authenticated,
        status="authenticated" if status.authenticated else "login_required",
        message=status.message,
    )


@get("/health")
async def health(request: Request) -> dict[str, str]:
    if request.headers.get("origin") != request.app.state.settings.web_origin:
        raise NotAuthorizedException(detail="Origem não permitida")
    return {"status": "ok"}


api_router = Router(
    path="/v1",
    route_handlers=[
        health,
        notebooklm_status,
        notebooklm_login,
        create_job,
        get_job,
        download_artifact,
    ],
    tags=["local companion"],
)
