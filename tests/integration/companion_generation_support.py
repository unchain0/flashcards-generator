from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import anyio
import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from flashcards_generator.delivery.companion.app import create_app
from flashcards_generator.delivery.companion.config import CompanionSettings
from flashcards_generator.delivery.companion.profiles import (
    NotebookLMProfiles,
)
from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.dto.workflow import AuthStatus

ORIGIN = "https://flashcards.example.com"
USER_ID = "a" * 32
OTHER_USER_ID = "b" * 32


class WorkflowStub:
    authenticated = True
    generate_operation: Callable[
        [GenerateFlashcardsRequest, ProgressReporter, CancellationToken],
        GenerationOutcome,
    ]

    def auth_status(self) -> AuthStatus:
        return AuthStatus(self.authenticated, "authenticated")

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        return self.generate_operation(request, reporter, token)


@pytest.fixture
async def companion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncTestClient[Litestar], WorkflowStub]]:
    workflow = WorkflowStub()

    def profile_for_user(
        _profiles: NotebookLMProfiles, _user_id: str
    ) -> WorkflowStub:
        return workflow

    async def verify(token: str, _settings: CompanionSettings) -> str | None:
        if token == "valid-capability":
            return USER_ID
        if token == "other-capability":
            return OTHER_USER_ID
        return None

    monkeypatch.setattr(NotebookLMProfiles, "for_user", profile_for_user)
    app = create_app(
        CompanionSettings(web_origin=ORIGIN, data_dir=tmp_path),
        token_verifier=verify,
    )
    async with AsyncTestClient(
        app,
        base_url="http://127.0.0.1:8765",
    ) as client:
        yield client, workflow


def auth_headers(token: str = "valid-capability") -> dict[str, str]:
    return {"Origin": ORIGIN, "Authorization": f"Bearer {token}"}


def successful_generation(
    request: GenerateFlashcardsRequest,
    reporter: ProgressReporter,
    token: CancellationToken,
) -> GenerationOutcome:
    token.raise_if_cancelled()
    uploaded = list(request.input_dir.iterdir())
    assert len(uploaded) == 1
    assert uploaded[0].read_bytes() == b"%PDF-1.7"
    assert uploaded[0].name == "01-lesson.pdf"
    output = request.output_dir / "lesson.csv"
    output.write_text("Text,Extra\n{{c1::lesson}},review\n", encoding="utf-8")
    for stage in ProgressStage:
        reporter.publish(
            ProgressEvent(
                stage=stage,
                state=ProgressState.ADVANCED,
                message="progress",
            )
        )
    for progress_state in ProgressState:
        reporter.publish(
            ProgressEvent(
                stage=ProgressStage.SOURCE,
                state=progress_state,
                message="progress",
            )
        )
    reporter.publish(
        ProgressEvent(
            stage=ProgressStage.DISCOVERY,
            state=ProgressState.COMPLETED,
            message="Source discovery completed",
            total=1,
        )
    )
    reporter.publish(
        ProgressEvent(
            stage=ProgressStage.SOURCE,
            state=ProgressState.COMPLETED,
            message="Source completed",
        )
    )
    return GenerationOutcome(
        decks=(),
        discovered_sources=1,
        completed_sources=1,
        skipped_sources=0,
        failed_sources=(),
        csv_paths=(output,),
    )


async def wait_for_terminal_job(
    client: AsyncTestClient[Litestar], job_id: str
) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(
            f"/v1/jobs/{job_id}", headers=auth_headers()
        )
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        await anyio.sleep(0.01)
    raise AssertionError("A geração local não terminou no prazo de teste.")
