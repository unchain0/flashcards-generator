from __future__ import annotations

from pathlib import Path

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from tests.integration.companion_generation_support import (
    WorkflowStub,
    auth_headers,
    successful_generation,
    wait_for_terminal_job,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_generation_stays_local_and_downloads_csv(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    tmp_path: Path,
) -> None:
    client, workflow = companion
    workflow.generate_operation = successful_generation

    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        data={
            "language": "en",
            "difficulty": "hard",
            "quantity": "more",
            "timeout": "60",
            "instructions": "Use examples from the document.",
        },
        files=[("files", ("../lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    assert response.status_code == 202
    submitted = response.json()
    assert submitted["status"] in {"queued", "running"}
    assert submitted["filenames"] == ["01-lesson.pdf"]
    job = await wait_for_terminal_job(client, submitted["id"])

    assert job["status"] == "completed"
    assert job["discovered_sources"] == 1
    assert job["completed_sources"] == 1
    artifact = job["artifacts"][0]
    downloaded = await client.get(artifact["url"], headers=auth_headers())
    assert downloaded.status_code == 200
    assert downloaded.content == b"Text,Extra\n{{c1::lesson}},review\n"
    assert not (tmp_path / "jobs").exists()


async def test_generation_rejects_unavailable_notebooklm_before_upload(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, workflow = companion
    workflow.authenticated = False

    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )

    assert response.status_code == 409
    assert "Conecte sua conta" in response.json()["detail"]


async def test_generation_rejects_unsupported_empty_and_invalid_uploads(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, _workflow = companion
    no_files = await client.post("/v1/jobs", headers=auth_headers())
    unsupported = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("notes.txt", b"text", "text/plain"))],
    )
    empty = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("empty.pdf", b"", "application/pdf"))],
    )
    invalid_language = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        data={"language": "en;touch"},
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    too_many = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[
            ("files", (f"lesson-{index}.pdf", b"%PDF-1.7", "application/pdf"))
            for index in range(11)
        ],
    )

    assert no_files.status_code == 400, no_files.text
    assert unsupported.status_code == 400, unsupported.text
    assert empty.status_code == 400, empty.text
    assert invalid_language.status_code == 400, invalid_language.text
    assert too_many.status_code == 400, too_many.text


async def test_generation_rejects_malformed_form_fields(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, _workflow = companion
    plain_text_as_file = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        data={"files": "not-an-upload"},
    )
    unknown_field = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        data={"unexpected": "value"},
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    repeated_option = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[
            ("language", (None, "en")),
            ("language", (None, "pt_BR")),
            ("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf")),
        ],
    )

    assert plain_text_as_file.status_code == 400
    assert unknown_field.status_code == 400
    assert repeated_option.status_code == 400


async def test_companion_health_accepts_only_the_configured_origin(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, _workflow = companion

    allowed = await client.get(
        "/v1/health", headers={"Origin": "https://flashcards.example.com"}
    )
    denied = await client.get(
        "/v1/health", headers={"Origin": "https://attacker.example"}
    )

    assert allowed.status_code == 200
    assert allowed.json() == {"status": "ok"}
    assert denied.status_code == 401


async def test_generation_enforces_local_file_size_limit(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _workflow = companion
    monkeypatch.setattr(
        "flashcards_generator.delivery.companion.generation_uploads.MAX_PDF_FILE_BYTES",
        2,
    )

    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"123", "application/pdf"))],
    )

    assert response.status_code == 413
    assert (
        response.json()["detail"]
        == "Os arquivos excedem o limite de tamanho permitido."
    )


async def test_generation_enforces_total_upload_size_limit(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _workflow = companion
    monkeypatch.setattr(
        "flashcards_generator.delivery.companion.generation_uploads.MAX_JOB_UPLOAD_BYTES",
        2,
    )

    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"123", "application/pdf"))],
    )

    assert response.status_code == 413
    assert (
        response.json()["detail"]
        == "Os arquivos excedem o limite de tamanho permitido."
    )
