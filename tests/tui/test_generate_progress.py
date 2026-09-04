"""Failing-first Pilot coverage for generation and cancellation."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import anyio
import pytest

from flashcards_generator.application.contracts import (
    CancellationToken,
    GenerationOutcome,
    ProgressEvent,
    ProgressReporter,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.domain.entities import Deck
from flashcards_generator.domain.exceptions import OperationCancelled
from flashcards_generator.interfaces.tui.app import FlashcardsApp


class BlockingGenerationService:
    """Fake provider that blocks until the UI cancels its token."""

    def __init__(self) -> None:
        self.started = Event()
        self.cancelled = Event()
        self.calls = 0
        self.request: GenerateFlashcardsRequest | None = None

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        self.calls += 1
        self.request = request
        self.started.set()
        reporter.publish(
            ProgressEvent(
                stage=ProgressStage.DISCOVERY,
                state=ProgressState.STARTED,
                message="discovering sources",
                current=0,
                total=2,
            )
        )
        try:
            token.wait_or_cancel(30)
        except OperationCancelled:
            self.cancelled.set()
            reporter.publish(
                ProgressEvent(
                    stage=ProgressStage.GENERATION,
                    state=ProgressState.FAILED,
                    message="cancelled",
                )
            )
            raise
        return GenerationOutcome(
            decks=(Deck(name="fake"),),
            discovered_sources=2,
            completed_sources=2,
            skipped_sources=0,
            failed_sources=(),
        )


class GenerationServices:
    """Minimal injected service bundle for the generation Pilot."""

    def __init__(self, generator: BlockingGenerationService) -> None:
        self.generator = generator

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        return self.generator.generate(request, reporter, token)

    def cancel_management(self) -> None:
        return


class ImmediateGenerationService:
    """Fake successful generation boundary for form-option assertions."""

    def __init__(self) -> None:
        self.completed = Event()
        self.calls = 0
        self.request: GenerateFlashcardsRequest | None = None

    def generate(
        self,
        request: GenerateFlashcardsRequest,
        reporter: ProgressReporter,
        token: CancellationToken,
    ) -> GenerationOutcome:
        self.calls += 1
        self.request = request
        self.completed.set()
        return GenerationOutcome(
            decks=(Deck(name="fake"),),
            discovered_sources=1,
            completed_sources=1,
            skipped_sources=0,
            failed_sources=(),
        )


@pytest.mark.anyio
async def test_generate_builds_request_and_cancels_one_worker(
    tmp_path: Path,
) -> None:
    """Given PDF/PPTX sources, one worker starts and cancellation reaps it."""
    input_dir = tmp_path / "fontes com espaço"
    input_dir.mkdir()
    (input_dir / "aula.pdf").touch()
    (input_dir / "slides.pptx").touch()
    output_dir = tmp_path / "saída"
    generator = BlockingGenerationService()
    app = FlashcardsApp(services=GenerationServices(generator))

    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#generate-input-dir").value = str(input_dir)
        app.query_one("#generate-output-dir").value = str(output_dir)
        await pilot.click("#generate-refresh")
        await pilot.click("#generate-select-all")
        await pilot.click("#generate-start")

        assert await anyio.to_thread.run_sync(generator.started.wait, 5)
        assert generator.request is not None
        assert (
            generator.calls,
            isinstance(generator.request, GenerateFlashcardsRequest),
            generator.request.explicit_files,
            app.generation_worker_count,
        ) == (
            1,
            True,
            [
                str(input_dir / "aula.pdf"),
                str(input_dir / "slides.pptx"),
            ],
            1,
        )

        await pilot.click("#progress-cancel")
        cancelled = await anyio.to_thread.run_sync(generator.cancelled.wait, 5)
        assert (
            cancelled,
            app.active_generation_worker,
            "cancel"
            in str(app.query_one("#progress-status").render()).lower(),
        ) == (True, False, True)


@pytest.mark.anyio
async def test_generate_form_maps_advanced_options_to_real_request(
    tmp_path: Path,
) -> None:
    """Given advanced values, the TUI builds one complete request DTO."""
    input_dir = tmp_path / "sources"
    input_dir.mkdir()
    (input_dir / "lesson.pdf").touch()
    service = ImmediateGenerationService()
    app = FlashcardsApp(services=GenerationServices(service))

    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#generate-input-dir").value = str(input_dir)
        app.query_one("#generate-output-dir").value = str(tmp_path / "out")
        app.query_one("#generate-difficulty").value = "hard"
        app.query_one("#generate-quantity").value = "more"
        app.query_one("#generate-language").value = "en"
        app.query_one("#generate-instructions").value = "Use examples"
        app.query_one("#generate-include").value = "*.pdf"
        app.query_one("#generate-exclude").value = "draft"
        app.query_one("#generate-timeout").value = "120"
        app.query_one("#generate-no-wait").value = True
        await pilot.click("#generate-refresh")
        await pilot.click("#generate-select-all")
        await pilot.click("#generate-start")

        assert await anyio.to_thread.run_sync(service.completed.wait, 5)
        assert service.request is not None
        assert (
            service.request.difficulty,
            service.request.quantity,
            service.request.language,
            service.request.instructions,
            service.request.include_pattern,
            service.request.exclude_pattern,
            service.request.timeout,
            service.request.wait_for_completion,
            service.request.explicit_files,
        ) == (
            "hard",
            "more",
            "en",
            "Use examples",
            "*.pdf",
            "draft",
            120,
            False,
            [str(input_dir / "lesson.pdf")],
        )


@pytest.mark.anyio
async def test_generate_rejects_invalid_advanced_option_without_worker(
    tmp_path: Path,
) -> None:
    """Given an invalid timeout, the TUI reports validation and does not run."""
    service = ImmediateGenerationService()
    app = FlashcardsApp(services=GenerationServices(service))

    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#generate-input-dir").value = str(tmp_path)
        app.query_one("#generate-timeout").value = "not-a-number"
        await pilot.click("#generate-start")

        assert service.calls == 0
        assert (
            "invalid"
            in str(app.query_one("#progress-status").render()).lower()
        )
