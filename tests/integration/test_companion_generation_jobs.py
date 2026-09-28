from __future__ import annotations

from pathlib import Path

import pytest
from litestar import Litestar
from litestar.datastructures import UploadFile
from litestar.testing import AsyncTestClient

from flashcards_generator.delivery.companion import (
    generation as generation_module,
)
from flashcards_generator.services import (
    local_generation_jobs as job_service,
)
from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressReporter,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.dto.local_generation import (
    GenerationOptions,
)
from flashcards_generator.services.local_generation_jobs import (
    JOB_RETENTION_SECONDS,
    MAX_PENDING_JOBS,
    MAX_RETAINED_JOBS,
    PendingJobLimitError,
    RetainedJobLimitError,
)
from tests.integration.companion_generation_support import (
    USER_ID,
    WorkflowStub,
    auth_headers,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_generation_manager_rejects_closed_jobs(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, _workflow = companion
    jobs = client.app.state.jobs
    jobs.close()
    closed = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )

    assert closed.status_code == 503
    assert (
        closed.json()["detail"]
        == "O auxiliar local está encerrando. Tente novamente."
    )
    jobs.close()


async def test_generation_close_during_upload_discards_unsubmitted_workspace(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _workflow = companion
    jobs = client.app.state.jobs
    save_uploads = generation_module.save_uploads
    input_dirs: list[Path] = []

    async def save_and_close(
        input_dir: Path,
        uploads: list[UploadFile],
        filenames: list[str],
    ) -> None:
        input_dirs.append(input_dir)
        await save_uploads(input_dir, uploads, filenames)
        jobs.close()

    monkeypatch.setattr(generation_module, "save_uploads", save_and_close)
    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )

    assert response.status_code == 503
    assert len(input_dirs) == 1
    assert not input_dirs[0].exists()


async def test_generation_manager_enforces_pending_limit(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, _workflow = companion
    jobs = client.app.state.jobs
    pending = [
        jobs._new_job(USER_ID, [f"lesson-{index}.pdf"], GenerationOptions())
        for index in range(MAX_PENDING_JOBS)
    ]
    with pytest.raises(PendingJobLimitError):
        jobs._new_job(USER_ID, ["fourth.pdf"], GenerationOptions())

    assert len(pending) == MAX_PENDING_JOBS
    jobs.close()


async def test_generation_manager_rejects_when_all_retained_jobs_are_pending(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _workflow = companion
    jobs = client.app.state.jobs
    monkeypatch.setattr(job_service, "MAX_PENDING_JOBS", MAX_RETAINED_JOBS + 1)
    pending = [
        jobs._new_job(USER_ID, [f"lesson-{index}.pdf"], GenerationOptions())
        for index in range(MAX_RETAINED_JOBS)
    ]

    with pytest.raises(RetainedJobLimitError):
        jobs._new_job(USER_ID, ["next.pdf"], GenerationOptions())

    assert len(pending) == MAX_RETAINED_JOBS
    jobs.close()


async def test_generation_manager_marks_a_pre_cancelled_job_terminal(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, _workflow = companion
    jobs = client.app.state.jobs
    job = jobs._new_job(USER_ID, ["lesson.pdf"], GenerationOptions())
    job.token.cancel()

    jobs._run(job)

    assert jobs.get(USER_ID, job.id).status == "cancelled"
    jobs.close()


async def test_generation_manager_logs_failed_input_cleanup(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, workflow = companion
    jobs = client.app.state.jobs

    def completed_generation(
        request: GenerateFlashcardsRequest,
        _reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        token.raise_if_cancelled()
        output = request.output_dir / "lesson.csv"
        output.write_text("Text,Extra\nterm,definition\n", encoding="utf-8")
        return GenerationOutcome((), 1, 1, 0, (), (output,))

    class RemovalFailure:
        @staticmethod
        def rmtree(_path: Path) -> None:
            raise PermissionError("input cleanup is unavailable")

    workflow.generate_operation = completed_generation
    job = jobs._new_job(USER_ID, ["lesson.pdf"], GenerationOptions())
    job.input_dir.joinpath(job.filenames[0]).write_bytes(b"%PDF-1.7")
    with monkeypatch.context() as cleanup_patch:
        cleanup_patch.setattr(job_service, "shutil", RemovalFailure)
        jobs._run(job)

    assert job.status == "completed"
    assert job.input_dir.exists()
    workspace = Path(job.workspace.name)
    jobs.close()
    assert not workspace.exists()


async def test_generation_manager_ignores_missing_inputs_during_cleanup(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, workflow = companion
    jobs = client.app.state.jobs

    def completed_generation(
        _request: GenerateFlashcardsRequest,
        _reporter: ProgressReporter,
        _token: CancellationToken,
    ) -> GenerationOutcome:
        return GenerationOutcome((), 0, 0, 0, (), ())

    class MissingInput:
        @staticmethod
        def rmtree(_path: Path) -> None:
            raise FileNotFoundError

    workflow.generate_operation = completed_generation
    job = jobs._new_job(USER_ID, ["lesson.pdf"], GenerationOptions())
    workspace = Path(job.workspace.name)
    monkeypatch.setattr(job_service, "shutil", MissingInput)

    jobs._run(job)

    assert job.status == "completed"
    assert not workspace.exists()


async def test_generation_manager_discards_oldest_retained_job(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _workflow = companion
    retained_jobs = client.app.state.jobs
    monkeypatch.setattr(job_service, "MAX_RETAINED_JOBS", 1)
    first = retained_jobs._new_job(USER_ID, ["first.pdf"], GenerationOptions())
    retained_jobs._finish(first, "completed", "done")
    second = retained_jobs._new_job(
        USER_ID, ["second.pdf"], GenerationOptions()
    )

    assert retained_jobs.get(USER_ID, first.id) is None
    assert retained_jobs.get(USER_ID, second.id) is not None
    retained_jobs.close()


async def test_generation_manager_cleans_expired_state_and_is_idempotent(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _workflow = companion
    jobs = client.app.state.jobs
    clock = [100.0]
    monkeypatch.setattr(job_service, "monotonic", lambda: clock[0])
    job = jobs._new_job(USER_ID, ["lesson.pdf"], GenerationOptions())
    jobs._finish(job, "completed", "done")

    jobs._expire(job.id)
    assert jobs.get(USER_ID, job.id) is not None
    clock[0] += JOB_RETENTION_SECONDS + 1
    jobs._expire(job.id)
    assert jobs.get(USER_ID, job.id) is None

    expired = jobs._new_job(USER_ID, ["expired.pdf"], GenerationOptions())
    jobs._finish(expired, "completed", "done")
    clock[0] += JOB_RETENTION_SECONDS + 1
    assert jobs.get(USER_ID, expired.id) is None
    jobs.close()
    jobs.close()
