"""Tests for uncovered lines in use_cases.py (100% coverage)."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flashcards_generator.domain_models.entities import Deck, Flashcard
from flashcards_generator.domain_models.exceptions import OperationCancelled
from flashcards_generator.integrations import source_snapshot
from flashcards_generator.integrations.chunk_state_repository import (
    FileSystemChunkStateRepository,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.use_cases import (
    GenerateFlashcardsUseCase,
)
from tests.fixtures.use_case_fixtures import make_use_case


def _make_pdf_entry(
    tmp_path: Path, mock_generator
) -> tuple[
    GenerateFlashcardsUseCase,
    Path,
    Path,
    Path,
    GenerateFlashcardsRequest,
]:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_path = input_dir / "source.pdf"
    pdf_path.write_bytes(b"source bytes")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    request = GenerateFlashcardsRequest(
        input_dir=input_dir,
        output_dir=output_dir,
    )
    use_case = make_use_case(generator=mock_generator())
    return use_case, pdf_path, input_dir, output_dir, request


class TestSafePdfPathEdgeCases:
    """Test _is_safe_file_path edge cases for 100% coverage."""

    def test_is_safe_file_path_rejects_symlink(self, tmp_path, mock_generator):
        """Test that symlinks are rejected (lines 195-196)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        # Create a real PDF file
        real_pdf = input_dir / "real.pdf"
        real_pdf.write_text("PDF content")

        # Create a symlink to the PDF
        symlink_pdf = input_dir / "symlink.pdf"
        symlink_pdf.symlink_to(real_pdf)

        use_case = make_use_case(generator=mock_generator())

        # Symlink should be rejected
        result = use_case._is_safe_file_path(symlink_pdf, input_dir)
        assert result is False

    def test_corrupt_pdf_does_not_leave_resume_lock(
        self, tmp_path, mock_generator
    ):
        """Non-chunked failures must not publish a resume lock artifact."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "corrupt.pdf").write_bytes(b"not a PDF")

        use_case = make_use_case(
            generator=mock_generator(should_fail_source=True),
            chunk_state_repository=FileSystemChunkStateRepository(),
        )
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        assert use_case.execute(request) == []
        assert use_case.last_run_had_errors is True
        assert not (
            output_dir / ".flashcards_resume" / ".corrupt.lock"
        ).exists()

    def test_oversized_pdf_fails_closed_without_provider_call(
        self, tmp_path, mock_generator, monkeypatch
    ):
        """Resource-limit failures must not reach the provider boundary."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "oversized.pdf").write_bytes(b"012345")

        generator = mock_generator()
        use_case = make_use_case(generator=generator)
        monkeypatch.setattr(use_case.pdf_chunker, "MAX_PDF_FILE_BYTES", 5)
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            resume=True,
        )

        assert use_case.execute(request) == []
        assert use_case.last_run_had_errors is True
        assert generator._notebooks == {}

    def test_is_safe_file_path_rejects_non_pdf(self, tmp_path, mock_generator):
        """Test that non-PDF files are rejected (lines 216-217)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        # Create a text file (not PDF)
        text_file = input_dir / "document.txt"
        text_file.write_text("Not a PDF")

        use_case = make_use_case(generator=mock_generator())

        # Non-PDF should be rejected
        result = use_case._is_safe_file_path(text_file, input_dir)
        assert result is False

    def test_is_safe_file_path_outside_directory(
        self, tmp_path, mock_generator
    ):
        """Test that paths outside input directory are rejected (lines 205-207)."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        # Create a PDF in another directory
        other_pdf = other_dir / "other.pdf"
        other_pdf.write_text("PDF content")

        use_case = make_use_case(generator=mock_generator())

        # PDF outside input directory should be rejected
        result = use_case._is_safe_file_path(other_pdf, input_dir)
        assert result is False

    def test_snapshot_source_fchmod_failure_closes_directory_descriptor(
        self, tmp_path, mock_generator, monkeypatch
    ):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        pdf_path = input_dir / "source.pdf"
        pdf_path.write_bytes(b"PDF content")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        pdf_output_path = output_dir / "source"
        pdf_output_path.mkdir()

        open_file = os.open
        directory_descriptor: int | None = None
        fchmod_descriptor: int | None = None

        def capture_directory_open(
            path: str | os.PathLike[str],
            flags: int,
            *args: int,
            **kwargs: int,
        ) -> int:
            nonlocal directory_descriptor
            descriptor = open_file(path, flags, *args, **kwargs)
            if flags & os.O_DIRECTORY:
                directory_descriptor = descriptor
            return descriptor

        def fail_fchmod(descriptor: int, mode: int) -> None:
            nonlocal fchmod_descriptor
            fchmod_descriptor = descriptor
            raise OSError("chmod failed")

        monkeypatch.setattr(source_snapshot.os, "open", capture_directory_open)
        monkeypatch.setattr(source_snapshot.os, "fchmod", fail_fchmod)
        use_case = make_use_case(generator=mock_generator())

        assert use_case._snapshot_source(pdf_path, pdf_output_path) is None
        assert directory_descriptor is not None
        assert fchmod_descriptor == directory_descriptor

        try:
            os.fstat(directory_descriptor)
        except OSError:
            descriptor_is_closed = True
        else:
            os.close(directory_descriptor)
            descriptor_is_closed = False

        assert descriptor_is_closed, "fchmod failure leaked the directory fd"


class TestGetOutputSubdirEdgeCases:
    def test_output_symlink_escape_stops_before_snapshot_or_generation(
        self, tmp_path, mock_generator
    ):
        input_dir = tmp_path / "input"
        pdf_file = input_dir / "tema" / "sub" / "documento.pdf"
        pdf_file.parent.mkdir(parents=True)
        pdf_file.write_bytes(b"PDF content")
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        (output_dir / "tema").symlink_to(
            external_dir, target_is_directory=True
        )

        generator = mock_generator()
        use_case = make_use_case(generator=generator)
        snapshot = MagicMock(wraps=use_case._snapshot_source)
        use_case._snapshot_source = snapshot
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        with pytest.raises(OSError, match="escaped result root"):
            use_case.execute(request)

        snapshot.assert_not_called()
        assert generator._notebooks == {}
        assert not (external_dir / "sub").exists()

    def test_get_output_subdir_empty_parts(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "test.pdf"
        pdf_file.write_text("PDF content")

        use_case = make_use_case(generator=mock_generator())
        result = use_case._get_output_subdir(pdf_file, input_dir, output_dir)

        assert result == output_dir
        assert result.exists()


class TestProcessPdfEntrySnapshotLifecycle:
    def test_process_entry_receives_and_removes_real_snapshot(
        self, tmp_path, mock_generator
    ) -> None:
        use_case, pdf_path, input_dir, output_dir, request = _make_pdf_entry(
            tmp_path, mock_generator
        )
        deck = Deck(
            name="source",
            flashcards=[
                Flashcard(front="A useful fact", back="A clear answer")
            ],
        )

        def process_snapshot(*args: object) -> Deck:
            snapshot_path = args[-1]
            assert isinstance(snapshot_path, Path)
            assert snapshot_path.read_bytes() == pdf_path.read_bytes()
            return deck

        process = MagicMock(side_effect=process_snapshot)
        use_case._process_pdf_with_lock = process

        result = use_case._process_pdf_entry(
            pdf_path, input_dir, output_dir, request
        )

        assert result is deck
        process.assert_called_once()
        snapshot_path = process.call_args.args[-1]
        assert snapshot_path != pdf_path
        assert not snapshot_path.exists()
        assert not snapshot_path.parent.exists()

    @pytest.mark.parametrize(
        "cancelled", [False, True], ids=["error", "cancel"]
    )
    def test_process_entry_preserves_primary_error_after_snapshot_cleanup(
        self, tmp_path, mock_generator, cancelled: bool
    ) -> None:
        use_case, pdf_path, input_dir, output_dir, request = _make_pdf_entry(
            tmp_path, mock_generator
        )
        processing_error = (
            OperationCancelled()
            if cancelled
            else RuntimeError("processing failed")
        )
        process = MagicMock(side_effect=processing_error)
        use_case._process_pdf_with_lock = process

        with pytest.raises(type(processing_error)) as result:
            use_case._process_pdf_entry(
                pdf_path, input_dir, output_dir, request
            )

        assert result.value is processing_error
        process.assert_called_once()
        snapshot_path = process.call_args.args[-1]
        assert not snapshot_path.exists()
        assert not snapshot_path.parent.exists()

    @pytest.mark.parametrize(
        "cancelled", [False, True], ids=["error", "cancel"]
    )
    def test_process_entry_preserves_primary_error_when_snapshot_unlink_fails(
        self, tmp_path, mock_generator, monkeypatch, cancelled: bool
    ) -> None:
        use_case, pdf_path, input_dir, output_dir, request = _make_pdf_entry(
            tmp_path, mock_generator
        )
        processing_error = (
            OperationCancelled()
            if cancelled
            else RuntimeError("processing failed")
        )
        unlink_error = OSError("SECRET_UNLINK_DETAIL")
        process = MagicMock(side_effect=processing_error)
        use_case._process_pdf_with_lock = process
        unlink = Path.unlink

        def fail_snapshot_unlink(
            path: Path, *, missing_ok: bool = False
        ) -> None:
            if path.parent.name == ".flashcards_sources":
                raise unlink_error
            unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_snapshot_unlink)

        with pytest.raises(type(processing_error)) as result:
            use_case._process_pdf_entry(
                pdf_path, input_dir, output_dir, request
            )

        assert result.value is processing_error
        assert any("OSError" in note for note in processing_error.__notes__)
        assert all(
            "SECRET_UNLINK_DETAIL" not in note
            for note in processing_error.__notes__
        )
        snapshot_path = process.call_args.args[-1]
        assert snapshot_path.exists()
        monkeypatch.setattr(Path, "unlink", unlink)
        snapshot_path.unlink()

    def test_process_entry_propagates_only_snapshot_unlink_failure(
        self, tmp_path, mock_generator, monkeypatch
    ) -> None:
        use_case, pdf_path, input_dir, output_dir, request = _make_pdf_entry(
            tmp_path, mock_generator
        )
        deck = Deck(
            name="source",
            flashcards=[
                Flashcard(front="A useful fact", back="A clear answer")
            ],
        )
        process = MagicMock(return_value=deck)
        use_case._process_pdf_with_lock = process
        unlink_error = OSError("snapshot unlink failed")
        unlink = Path.unlink

        def fail_snapshot_unlink(
            path: Path, *, missing_ok: bool = False
        ) -> None:
            if path.parent.name == ".flashcards_sources":
                raise unlink_error
            unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_snapshot_unlink)

        with pytest.raises(OSError) as result:
            use_case._process_pdf_entry(
                pdf_path, input_dir, output_dir, request
            )

        assert result.value is unlink_error
        process.assert_called_once()
        snapshot_path = process.call_args.args[-1]
        monkeypatch.setattr(Path, "unlink", unlink)
        snapshot_path.unlink()

    def test_process_entry_skips_processing_when_snapshot_creation_fails(
        self, tmp_path, mock_generator, monkeypatch
    ) -> None:
        use_case, pdf_path, input_dir, output_dir, request = _make_pdf_entry(
            tmp_path, mock_generator
        )
        process = MagicMock()
        use_case._process_pdf_with_lock = process

        def fail_snapshot(_source: Path, _output: Path) -> Path:
            raise OSError("snapshot creation failed")

        monkeypatch.setattr(
            "flashcards_generator.integrations.source_snapshot.create_source_snapshot",
            fail_snapshot,
        )

        assert (
            use_case._process_pdf_entry(
                pdf_path, input_dir, output_dir, request
            )
            is None
        )
        assert use_case._last_pdf_had_error is True
        process.assert_not_called()


class TestProcessPdfRuntimeError:
    def test_process_pdf_runtime_error(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "test.pdf"
        pdf_file.write_text("PDF content")

        generator = mock_generator()
        use_case = make_use_case(generator=generator)
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
        use_case._create_notebook = MagicMock(
            side_effect=RuntimeError("Test error")
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        result = use_case._process_pdf(
            pdf_file, input_dir, output_dir, request
        )
        assert result is None

    def test_process_pdf_unexpected_error(self, tmp_path, mock_generator):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        pdf_file = input_dir / "test.pdf"
        pdf_file.write_text("PDF content")

        generator = mock_generator()
        use_case = make_use_case(generator=generator)
        use_case.pdf_chunker.needs_chunking = MagicMock(return_value=False)
        use_case._create_notebook = MagicMock(
            side_effect=TypeError("Unexpected error")
        )

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
        )

        with pytest.raises(TypeError, match="Unexpected error"):
            use_case._process_pdf(pdf_file, input_dir, output_dir, request)


class TestGenerationSafetyRegressions:
    def test_execute_rejects_source_revalidation_failure_and_processes_next(
        self, tmp_path, mock_generator, monkeypatch
    ) -> None:
        input_dir = tmp_path / "input"
        rejected = input_dir / "a-rejected.pdf"
        valid = input_dir / "b-valid.pdf"
        rejected.parent.mkdir()
        rejected.write_bytes(b"rejected source")
        valid.write_bytes(b"valid source")
        output_dir = tmp_path / "output"
        use_case = make_use_case(generator=mock_generator())
        deck = Deck(
            name="valid",
            flashcards=[
                Flashcard(front="A useful fact", back="A clear answer")
            ],
        )
        process = MagicMock(return_value=deck)
        use_case._process_pdf_with_lock = process
        snapshots: list[Path] = []
        create_snapshot = source_snapshot.create_source_snapshot

        def track_snapshot(source: Path, destination: Path) -> Path:
            snapshots.append(source)
            return create_snapshot(source, destination)

        monkeypatch.setattr(
            "flashcards_generator.integrations.source_snapshot.create_source_snapshot",
            track_snapshot,
        )
        original_stat = Path.stat
        rejected_stat_calls = 0

        def fail_rejected_revalidation(path, *, follow_symlinks=True):
            nonlocal rejected_stat_calls
            if path == rejected:
                rejected_stat_calls += 1
                if rejected_stat_calls == 2:
                    raise FileNotFoundError("source changed after discovery")
            return original_stat(path, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "stat", fail_rejected_revalidation)

        result = use_case.execute(
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir
            )
        )

        assert result == [deck]
        assert rejected_stat_calls == 2
        assert snapshots == [valid]
        process.assert_called_once()
        assert process.call_args.args[0] == valid

    def test_explicit_files_apply_discovery_safety_boundary(
        self, temp_dirs, mock_generator
    ) -> None:
        input_dir, output_dir = temp_dirs
        outside_pdf = input_dir.parent / "outside.pdf"
        outside_pdf.write_text("outside")
        (input_dir / "notes.txt").write_text("not a source")
        selected_pdf = input_dir / "selected.pdf"
        selected_pdf.write_text("selected")
        use_case = make_use_case(generator=mock_generator())

        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            explicit_files=["../outside.pdf", "notes.txt", "selected.pdf"],
        )

        selected = use_case._find_all_pdfs(input_dir, request)

        assert selected == [selected_pdf.resolve()]
        assert not (output_dir.parent / "outside.csv").exists()

    def test_input_swap_after_discovery_is_not_processed(
        self, temp_dirs, mock_generator, monkeypatch
    ) -> None:
        input_dir, output_dir = temp_dirs
        source = input_dir / "a.pdf"
        source.write_text("trusted")
        outside = input_dir.parent / "outside.pdf"
        outside.write_text("untrusted")
        use_case = make_use_case(generator=mock_generator())
        use_case._process_pdf = MagicMock(return_value=None)
        original_output_subdir = use_case._get_output_subdir

        def replace_after_discovery(
            pdf_path: Path, source_root: Path, result_root: Path
        ) -> Path:
            source.unlink()
            source.symlink_to(outside)
            return original_output_subdir(pdf_path, source_root, result_root)

        monkeypatch.setattr(
            use_case, "_get_output_subdir", replace_after_discovery
        )

        result = use_case.execute(
            GenerateFlashcardsRequest(
                input_dir=input_dir, output_dir=output_dir
            )
        )

        assert result == []
        use_case._process_pdf.assert_not_called()

    def test_background_generation_creates_no_completion_marker(
        self, temp_dirs, mock_generator
    ) -> None:
        input_dir, output_dir = temp_dirs
        source = input_dir / "file.pdf"
        source.write_text("source")
        use_case = make_use_case(generator=mock_generator())
        use_case._process_pdf = MagicMock(
            return_value=Deck(name="file", description="generating")
        )
        request = GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            wait_for_completion=False,
        )

        use_case.execute(request)
        use_case.execute(request)

        assert not (output_dir / "file.csv").exists()
        assert use_case._process_pdf.call_count == 2

    def test_normal_generation_deduplicates_before_export(
        self, tmp_path, mock_generator
    ) -> None:
        card = Flashcard(
            front="The {{c1::same fact}} has sufficient context for study.",
            back="The explanation contains enough detail for a useful card.",
        )
        generator = mock_generator()
        generator.parse_flashcards = MagicMock(
            return_value=[card, card.model_copy()]
        )
        use_case = make_use_case(generator=generator)

        deck = use_case._download_and_convert(
            "notebook",
            "artifact",
            tmp_path,
            "deck",
            "source",
        )

        assert len(deck.flashcards) == 1
