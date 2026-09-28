from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flashcards_generator.domain_models.entities import Deck, Flashcard
from flashcards_generator.domain_models.exceptions import (
    NotebookCleanupError,
    OperationCancelled,
)
from flashcards_generator.engines.cloze import ClozeConverter
from flashcards_generator.services import generation_artifact_execution
from flashcards_generator.services.contracts import (
    CancellationToken,
    ProgressEvent,
    ProgressStage,
    ProgressState,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)
from tests.fixtures.use_case_fixtures import make_use_case


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.events.append(event)


class RecordingArtifactContext:
    def __init__(self, generator: FlashcardGeneratorPort) -> None:
        self.generator = generator
        self.converter = ClozeConverter()
        self._created_notebooks: list[str] = []
        self.calls: list[str] = []
        self.config: GenerationConfig | None = None
        self.completion_arguments: tuple[object, ...] | None = None
        self.events: list[tuple[ProgressStage, ProgressState, int | None]] = []

    def _publish(
        self,
        stage: ProgressStage,
        state: ProgressState,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
        source: Path | None = None,
        chunk_index: int | None = None,
        cards: int | None = None,
    ) -> None:
        self.calls.append("publish")
        self.events.append((stage, state, cards))

    def _raise_if_cancelled(self) -> None:
        self.calls.append("cancel-check")

    def _handle_artifact_completion(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck:
        self.calls.append("completion")
        self.completion_arguments = (
            notebook_id,
            artifact_id,
            output_path,
            deck_name,
            request,
            pdf_stem,
        )
        return Deck(name=deck_name, notebook_id=notebook_id)

    def _download_and_convert(
        self,
        notebook_id: str,
        artifact_id: str,
        output_path: Path,
        deck_name: str,
        pdf_stem: str = "",
    ) -> Deck:
        raise AssertionError("Unexpected artifact download")

    def _download_flashcards(
        self, notebook_id: str, artifact_id: str, json_path: Path
    ) -> list[Flashcard]:
        raise AssertionError("Unexpected flashcard parsing")

    def _cleanup_raw_file(self, json_path: Path) -> None:
        raise AssertionError("Unexpected raw-file cleanup")

    def _convert_flashcards(
        self, flashcards: list[Flashcard], deck_name: str
    ) -> list[Flashcard]:
        raise AssertionError("Unexpected flashcard conversion")

    def _build_deck(
        self,
        notebook_id: str,
        deck_name: str,
        flashcards: list[Flashcard],
    ) -> Deck:
        raise AssertionError("Unexpected deck construction")

    def _delete_completed_notebook(self, notebook_id: str) -> None:
        raise AssertionError("Unexpected notebook deletion")


def test_typed_context_records_generation_config_and_dispatch_order(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    generator = mock_generator()
    context = RecordingArtifactContext(generator)

    def generate(notebook_id: str, config: GenerationConfig) -> str:
        context.calls.append("generate")
        context.config = config
        return "artifact"

    monkeypatch.setattr(generator, "generate_flashcards", generate)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path,
        difficulty="hard",
        quantity="detailed",
        instructions="custom instructions",
        timeout=321,
        wait_for_completion=True,
    )

    result = generation_artifact_execution.generate_flashcards(
        context,
        "notebook",
        "Lesson",
        tmp_path,
        request,
        "lesson",
        "default instructions",
    )

    assert result is not None
    assert context.config == GenerationConfig(
        difficulty="hard",
        quantity="detailed",
        instructions="custom instructions",
        timeout_seconds=321,
        wait_for_completion=True,
    )
    assert context.completion_arguments == (
        "notebook",
        "artifact",
        tmp_path,
        "Lesson",
        request,
        "lesson",
    )
    assert context.calls == [
        "publish",
        "cancel-check",
        "generate",
        "cancel-check",
        "completion",
        "publish",
    ]
    assert [event[1] for event in context.events] == [
        ProgressState.STARTED,
        ProgressState.COMPLETED,
    ]


@pytest.mark.parametrize(
    ("custom_instructions", "expected_instructions"),
    [("", "default instructions"), ("custom", "custom")],
)
def test_generate_flashcards_preserves_config_and_empty_completion(
    tmp_path: Path,
    mock_generator,
    monkeypatch,
    custom_instructions: str,
    expected_instructions: str,
) -> None:
    generator = mock_generator()
    monkeypatch.setattr(
        generator, "generate_flashcards", MagicMock(return_value="artifact")
    )
    context = make_use_case(generator=generator)
    reporter = RecordingReporter()
    context._reporter = reporter
    monkeypatch.setattr(context, "_raise_if_cancelled", MagicMock())
    empty_deck = Deck(name="Lesson", notebook_id="notebook")
    monkeypatch.setattr(
        context,
        "_handle_artifact_completion",
        MagicMock(return_value=empty_deck),
    )
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path,
        instructions=custom_instructions,
    )

    result = generation_artifact_execution.generate_flashcards(
        context,
        "notebook",
        "Lesson",
        tmp_path,
        request,
        "lesson",
        "default instructions",
    )

    assert result is empty_deck
    config = generator.generate_flashcards.call_args.args[1]
    assert config.instructions == expected_instructions
    assert [event.state.value for event in reporter.events] == [
        "started",
        "completed",
    ]


def test_no_wait_returns_empty_deck_without_waiting(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    generator = mock_generator()
    wait = MagicMock(return_value=True)
    monkeypatch.setattr(generator, "wait_for_artifact", wait)
    context = make_use_case(generator=generator)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path,
        wait_for_completion=False,
    )

    deck = generation_artifact_execution.handle_artifact_completion(
        context, "notebook", "artifact", tmp_path, "Lesson", request
    )

    assert deck is not None
    assert deck.flashcards == []
    wait.assert_not_called()


def test_artifact_timeout_returns_none_without_downloading(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    generator = mock_generator()
    monkeypatch.setattr(
        generator, "wait_for_artifact", MagicMock(return_value=False)
    )
    context = make_use_case(generator=generator)
    monkeypatch.setattr(context, "_raise_if_cancelled", MagicMock())
    download = MagicMock()
    monkeypatch.setattr(context, "_download_and_convert", download)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path, output_dir=tmp_path
    )

    result = generation_artifact_execution.handle_artifact_completion(
        context, "notebook", "artifact", tmp_path, "Lesson", request
    )

    assert result is None
    download.assert_not_called()


def test_cancellation_after_artifact_wait_prevents_download(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    token = CancellationToken()
    generator = mock_generator()
    monkeypatch.setattr(
        generator,
        "wait_for_artifact",
        lambda *_args, **_kwargs: token.cancel() or True,
    )
    context = make_use_case(generator=generator)
    context._token = token
    download = MagicMock()
    monkeypatch.setattr(context, "_download_and_convert", download)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path, output_dir=tmp_path
    )

    with pytest.raises(OperationCancelled):
        generation_artifact_execution.handle_artifact_completion(
            context, "notebook", "artifact", tmp_path, "Lesson", request
        )

    download.assert_not_called()


def test_parse_failure_still_removes_raw_artifact(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    generator = mock_generator()
    raw_path = tmp_path / "lesson_raw.json"
    monkeypatch.setattr(
        generator,
        "download_flashcards",
        lambda _notebook, _artifact, path: path.write_text("bad data"),
    )
    monkeypatch.setattr(
        generator,
        "parse_flashcards",
        MagicMock(side_effect=ValueError("invalid response")),
    )
    context = make_use_case(generator=generator)

    with pytest.raises(ValueError, match="invalid response"):
        generation_artifact_execution.download_flashcards(
            context, "notebook", "artifact", raw_path
        )

    assert not raw_path.exists()


def test_failed_notebook_deletion_keeps_notebook_tracked(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    generator = mock_generator()
    monkeypatch.setattr(
        generator,
        "delete_notebook",
        MagicMock(side_effect=NotebookCleanupError("notebook", "failed")),
    )
    context = make_use_case(generator=generator)
    context._created_notebooks.append("notebook")

    generation_artifact_execution.delete_completed_notebook(
        context, "notebook"
    )

    assert context._created_notebooks == ["notebook"]


def test_conversion_drops_cards_rejected_by_the_converter(
    mock_generator, monkeypatch
) -> None:
    context = make_use_case(generator=mock_generator())
    monkeypatch.setattr(
        context.converter, "convert", MagicMock(return_value=None)
    )
    card = Flashcard(front="Question", back="Answer")

    converted = generation_artifact_execution.convert_flashcards(
        context, [card], "Lesson"
    )

    assert converted == []


def test_use_case_generation_delegates_with_default_instructions(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    context = make_use_case(generator=mock_generator())
    delegate = MagicMock(return_value=None)
    monkeypatch.setattr(
        generation_artifact_execution, "generate_flashcards", delegate
    )
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path, output_dir=tmp_path
    )

    context._generate_flashcards(
        "notebook", "Lesson", tmp_path, request, "lesson"
    )

    assert delegate.call_args.args[0] is context
    assert delegate.call_args.args[-1] == context.DEFAULT_INSTRUCTIONS
