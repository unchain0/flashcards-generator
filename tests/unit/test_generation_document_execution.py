from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from flashcards_generator.domain_models.entities import Deck
from flashcards_generator.domain_models.exceptions import GenerationError
from flashcards_generator.integrations.pdf_utils import PDFChunker
from flashcards_generator.services import (
    generation_document_execution,
    generation_source_security,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.ports.flashcard_generator import (
    FlashcardGeneratorPort,
)
from tests.fixtures.use_case_fixtures import make_use_case


class RecordingDocumentContext:
    def __init__(
        self, output_exists: bool, generator: FlashcardGeneratorPort
    ) -> None:
        self.output_exists = output_exists
        self.generator = generator
        self._last_pdf_had_error = False
        self.pdf_chunker = PDFChunker()
        self.calls: list[str] = []

    def _get_deck_name(self, pdf_path: Path, input_path: Path) -> str:
        return "Lesson"

    def _get_output_subdir(
        self, pdf_path: Path, input_path: Path, output_path: Path
    ) -> Path:
        return output_path

    def _output_deck_exists(
        self, pdf_output_path: Path, pdf_stem: str
    ) -> bool:
        self.calls.append("exists")
        return self.output_exists

    def _log_pdf_header(
        self, pdf_path: Path, input_path: Path, deck_name: str
    ) -> None:
        self.calls.append("header")

    def _process_pdf_content(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        processing_path: Path,
    ) -> Deck | None:
        self.calls.append("content")
        return None

    def _log_pdf_processing_error(
        self,
        error: (GenerationError | OSError | ValueError | RuntimeError),
    ) -> None:
        raise AssertionError("Unexpected PDF processing error")

    def _should_chunk_pdf(self, pdf_path: Path, processing_path: Path) -> bool:
        raise AssertionError("Unexpected chunk decision")

    def _process_large_pdf(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        source_path: Path | None = None,
    ) -> Deck | None:
        raise AssertionError("Unexpected large PDF processing")

    def _process_regular_pdf(
        self,
        pdf_path: Path,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        processing_path: Path,
    ) -> Deck | None:
        raise AssertionError("Unexpected regular PDF processing")

    def _create_notebook(self, deck_name: str) -> str:
        raise AssertionError("Unexpected notebook creation")

    def _add_pdf_source(self, notebook_id: str, pdf_path: Path) -> str | None:
        raise AssertionError("Unexpected PDF source addition")

    def _raise_if_cancelled(self) -> None:
        raise AssertionError("Unexpected cancellation check")

    def _generate_flashcards(
        self,
        notebook_id: str,
        deck_name: str,
        pdf_output_path: Path,
        request: GenerateFlashcardsRequest,
        pdf_stem: str = "",
    ) -> Deck | None:
        raise AssertionError("Unexpected flashcard generation")


def test_process_pdf_uses_snapshot_and_skips_existing_csv(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    pdf_path = input_dir / "lesson.pdf"
    snapshot_path = tmp_path / "snapshot.pdf"
    request = GenerateFlashcardsRequest(
        input_dir=input_dir, output_dir=output_dir
    )
    context = make_use_case(generator=mock_generator())
    content = MagicMock(return_value=None)
    monkeypatch.setattr(context, "_get_deck_name", lambda *_: "Lesson")
    monkeypatch.setattr(context, "_get_output_subdir", lambda *_: output_dir)
    monkeypatch.setattr(context, "_process_pdf_content", content)

    generation_document_execution.process_pdf(
        context, pdf_path, input_dir, output_dir, request, snapshot_path
    )

    assert content.call_args.args[-1] == snapshot_path
    (output_dir / "lesson.csv").touch()
    content.reset_mock()

    result = generation_document_execution.process_pdf(
        context, pdf_path, input_dir, output_dir, request, snapshot_path
    )

    assert result is None
    content.assert_not_called()


def test_process_pdf_respects_facade_skip_override(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    context = make_use_case(generator=mock_generator())
    skip = MagicMock(return_value=True)
    header = MagicMock()
    content = MagicMock()
    monkeypatch.setattr(context, "_output_deck_exists", skip)
    monkeypatch.setattr(context, "_log_pdf_header", header)
    monkeypatch.setattr(context, "_process_pdf_content", content)
    request = GenerateFlashcardsRequest(
        input_dir=input_dir, output_dir=output_dir
    )

    result = context._process_pdf(
        input_dir / "lesson.pdf", input_dir, output_dir, request
    )

    assert result is None
    skip.assert_called_once_with(output_dir, "lesson")
    header.assert_not_called()
    content.assert_not_called()


def test_process_pdf_uses_facade_hooks_in_order_even_with_csv(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    (output_dir / "lesson.csv").touch()
    context = make_use_case(generator=mock_generator())
    calls: list[str] = []
    skip = MagicMock(side_effect=lambda *_: calls.append("exists") or False)
    header = MagicMock(side_effect=lambda *_: calls.append("header"))
    content = MagicMock(side_effect=lambda *_: calls.append("content"))
    monkeypatch.setattr(context, "_output_deck_exists", skip)
    monkeypatch.setattr(context, "_log_pdf_header", header)
    monkeypatch.setattr(context, "_process_pdf_content", content)
    request = GenerateFlashcardsRequest(
        input_dir=input_dir, output_dir=output_dir
    )

    context._process_pdf(
        input_dir / "lesson.pdf", input_dir, output_dir, request
    )

    skip.assert_called_once_with(output_dir, "lesson")
    header.assert_called_once_with(
        input_dir / "lesson.pdf", input_dir, "lesson"
    )
    assert calls == ["exists", "header", "content"]


def test_document_module_honors_typed_context_skip_contract(
    tmp_path: Path, mock_generator
) -> None:
    context = RecordingDocumentContext(
        output_exists=True, generator=mock_generator()
    )
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    request = GenerateFlashcardsRequest(
        input_dir=input_dir, output_dir=output_dir
    )

    result = generation_document_execution.process_pdf(
        context,
        input_dir / "lesson.pdf",
        input_dir,
        output_dir,
        request,
    )

    assert result is None
    assert context.calls == ["exists"]


def test_pdf_processing_rejects_an_unexpected_error_type(
    mock_generator,
) -> None:
    context = RecordingDocumentContext(
        output_exists=False, generator=mock_generator()
    )
    unexpected_error = cast(
        GenerationError | OSError | ValueError | RuntimeError,
        KeyError("unexpected"),
    )

    with pytest.raises(AssertionError):
        generation_document_execution.log_pdf_processing_error(
            context,
            unexpected_error,
        )

    assert context._last_pdf_had_error


def test_pdf_processing_skips_a_resume_lock_owned_by_another_worker(
    tmp_path: Path,
    mock_generator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_use_case(generator=mock_generator())
    monkeypatch.setattr(
        context, "_get_resume_lock", lambda *_: nullcontext(False)
    )
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path / "input", output_dir=tmp_path / "output"
    )

    result = generation_source_security.process_pdf_with_lock(
        context,
        tmp_path / "input" / "lesson.pdf",
        request.input_dir,
        request.output_dir,
        request.output_dir,
        request,
        tmp_path / "snapshot.pdf",
    )

    assert result is None


def test_pdf_chunking_uses_snapshot_but_never_chunks_pptx(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    context = make_use_case(generator=mock_generator())
    needs_chunking = MagicMock(return_value=True)
    monkeypatch.setattr(context.pdf_chunker, "needs_chunking", needs_chunking)
    pdf_path = tmp_path / "lesson.pdf"
    snapshot_path = tmp_path / "snapshot.pdf"

    assert not generation_document_execution.should_chunk_pdf(
        context, tmp_path / "lesson.pptx", snapshot_path
    )
    needs_chunking.assert_not_called()

    assert generation_document_execution.should_chunk_pdf(
        context, pdf_path, snapshot_path
    )
    assert needs_chunking.call_args.args[0] == snapshot_path


def test_pdf_content_keeps_chunk_dispatch_on_the_context(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    context = make_use_case(generator=mock_generator())
    deck = MagicMock()
    should_chunk = MagicMock(return_value=True)
    process_large = MagicMock(return_value=deck)
    process_regular = MagicMock()
    monkeypatch.setattr(context, "_should_chunk_pdf", should_chunk)
    monkeypatch.setattr(context, "_process_large_pdf", process_large)
    monkeypatch.setattr(context, "_process_regular_pdf", process_regular)
    processing_path = tmp_path / "snapshot.pdf"

    result = generation_document_execution.process_pdf_content(
        context,
        tmp_path / "lesson.pdf",
        "Lesson",
        tmp_path,
        GenerateFlashcardsRequest(input_dir=tmp_path, output_dir=tmp_path),
        processing_path,
    )

    assert result is deck
    assert process_large.call_args.args[-1] == processing_path
    process_regular.assert_not_called()


def test_use_case_process_pdf_delegates_to_document_module(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    context = make_use_case(generator=mock_generator())
    delegate = MagicMock(return_value=None)
    monkeypatch.setattr(generation_document_execution, "process_pdf", delegate)
    request = GenerateFlashcardsRequest(
        input_dir=tmp_path, output_dir=tmp_path
    )

    context._process_pdf(tmp_path / "lesson.pdf", tmp_path, tmp_path, request)

    assert delegate.call_args.args[0] is context


def test_regular_pdf_waits_for_snapshot_source_before_generation(
    tmp_path: Path, mock_generator, monkeypatch
) -> None:
    generator = mock_generator()
    context = make_use_case(generator=generator)
    source_path = tmp_path / "snapshot.pdf"
    pdf_path = tmp_path / "original.pdf"
    generate = MagicMock(return_value=None)
    monkeypatch.setattr(context, "_create_notebook", lambda _: "notebook")
    add_source = MagicMock(return_value="source")
    monkeypatch.setattr(context, "_add_pdf_source", add_source)
    monkeypatch.setattr(
        generator, "wait_for_source", MagicMock(return_value=True)
    )
    monkeypatch.setattr(context, "_generate_flashcards", generate)
    monkeypatch.setattr(context, "_raise_if_cancelled", MagicMock())

    request = GenerateFlashcardsRequest(
        input_dir=tmp_path,
        output_dir=tmp_path,
        timeout=321,
    )

    result = generation_document_execution.process_regular_pdf(
        context,
        pdf_path,
        "Lesson",
        tmp_path,
        request,
        source_path,
    )

    assert result is None
    assert add_source.call_args.args == ("notebook", source_path)
    generator.wait_for_source.assert_called_once_with(
        "notebook",
        "source",
        timeout=321,
    )
    generate.assert_called_once()
