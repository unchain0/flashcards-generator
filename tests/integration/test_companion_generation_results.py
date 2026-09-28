from __future__ import annotations

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from flashcards_generator.domain_models.exceptions import OperationCancelled
from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressReporter,
    SourceFailure,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from tests.integration.companion_generation_support import (
    WorkflowStub,
    auth_headers,
    wait_for_terminal_job,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_generation_failure_is_terminal_and_hides_provider_details(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, workflow = companion

    def fail_generation(
        _request: GenerateFlashcardsRequest,
        _reporter: ProgressReporter,
        _token: CancellationToken,
    ) -> GenerationOutcome:
        raise RuntimeError("private provider detail")

    workflow.generate_operation = fail_generation
    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    job = await wait_for_terminal_job(client, response.json()["id"])

    assert response.status_code == 202
    assert job["status"] == "failed"
    assert job["error"] == "Falha local: RuntimeError."
    assert "private provider detail" not in str(job)


async def test_generation_cancellation_is_reported_as_terminal(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, workflow = companion

    def cancel_generation(
        _request: GenerateFlashcardsRequest,
        _reporter: ProgressReporter,
        _token: CancellationToken,
    ) -> GenerationOutcome:
        raise OperationCancelled

    workflow.generate_operation = cancel_generation
    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    job = await wait_for_terminal_job(client, response.json()["id"])

    assert job["status"] == "cancelled"
    assert job["error"] is None


async def test_generation_reports_partial_source_failures_with_csv_artifact(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, workflow = companion

    def partial_generation(
        request: GenerateFlashcardsRequest,
        _reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        token.raise_if_cancelled()
        output = request.output_dir / "lesson.csv"
        output.write_text(
            "Text,Extra\n{{c1::lesson}},review\n", encoding="utf-8"
        )
        return GenerationOutcome(
            decks=(),
            discovered_sources=2,
            completed_sources=1,
            skipped_sources=0,
            failed_sources=(
                SourceFailure(
                    request.input_dir / "second.pdf", "source failed"
                ),
            ),
            csv_paths=(output,),
        )

    workflow.generate_operation = partial_generation
    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    job = await wait_for_terminal_job(client, response.json()["id"])

    assert job["status"] == "failed"
    assert job["failed_sources"] == 1
    assert job["error"] == "second.pdf: source failed"
    assert job["artifacts"][0]["name"] == "lesson.csv"


async def test_generation_rejects_artifacts_outside_its_private_output(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, workflow = companion

    def invalid_generation(
        request: GenerateFlashcardsRequest,
        _reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        token.raise_if_cancelled()
        outside = request.output_dir.parent / "outside.csv"
        outside.write_text("Text,Extra\nterm,definition\n", encoding="utf-8")
        return GenerationOutcome((), 1, 1, 0, (), (outside,))

    workflow.generate_operation = invalid_generation
    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    job = await wait_for_terminal_job(client, response.json()["id"])

    assert job["status"] == "failed"
    assert job["error"] == "Falha local: ValueError."
