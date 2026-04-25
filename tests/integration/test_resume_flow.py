"""Integration tests for the chunk resume flow."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pypdf import PdfWriter

from flashcards_generator.application.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.application.use_cases import (
    GenerateFlashcardsUseCase,
)
from flashcards_generator.domain.entities import (
    ChunkResumeManifest,
    ChunkState,
    ChunkStatus,
    Deck,
    Flashcard,
)
from flashcards_generator.domain.ports.flashcard_generator import (
    FlashcardGeneratorPort,
    GenerationConfig,
)
from flashcards_generator.infrastructure.chunk_state_repository import (
    FileSystemChunkStateRepository,
)

pytestmark = pytest.mark.integration


class ScriptedChunkGenerator(FlashcardGeneratorPort):
    """Port test double that returns chunk-specific flashcards."""

    def __init__(
        self,
        chunk_outcomes: dict[str, list[Flashcard] | Exception],
    ) -> None:
        self._chunk_outcomes = chunk_outcomes
        self._notebook_sources: dict[str, str] = {}
        self._artifact_cards: dict[str, list[Flashcard]] = {}
        self._notebook_counter = 0
        self._source_counter = 0
        self._artifact_counter = 0
        self.processed_source_names: list[str] = []

    def create_notebook(self, title: str) -> str:
        self._notebook_counter += 1
        return f"nb-{self._notebook_counter}"

    def add_source(self, notebook_id: str, pdf_path: Path) -> str:
        self._source_counter += 1
        self._notebook_sources[notebook_id] = pdf_path.name
        self.processed_source_names.append(pdf_path.name)
        return f"src-{self._source_counter}"

    def wait_for_source(
        self, notebook_id: str, source_id: str, timeout: int = 600
    ) -> bool:
        return True

    def generate_flashcards(
        self, notebook_id: str, config: GenerationConfig
    ) -> str | None:
        source_name = self._notebook_sources[notebook_id]
        outcome = self._chunk_outcomes[source_name]

        if isinstance(outcome, Exception):
            raise outcome

        self._artifact_counter += 1
        artifact_id = f"art-{self._artifact_counter}"
        self._artifact_cards[artifact_id] = outcome
        return artifact_id

    def wait_for_artifact(
        self, notebook_id: str, artifact_id: str, timeout: int = 900
    ) -> bool:
        return True

    def download_flashcards(
        self, notebook_id: str, artifact_id: str, output_path: Path
    ) -> bool:
        cards = self._artifact_cards[artifact_id]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps([card.model_dump() for card in cards]),
            encoding="utf-8",
        )
        return True

    def parse_flashcards(self, json_path: Path) -> list[Flashcard]:
        return [
            Flashcard.model_validate(card)
            for card in json.loads(json_path.read_text(encoding="utf-8"))
        ]

    def delete_notebook(self, notebook_id: str) -> bool:
        self._notebook_sources.pop(notebook_id, None)
        return True


@pytest.fixture(autouse=True)
def patch_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "flashcards_generator.application.use_cases.time.sleep",
        lambda _seconds: None,
    )


@pytest.fixture
def resume_paths(tmp_path: Path) -> dict[str, Path]:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    topic_dir = input_dir / "Tema1"
    pdf_path = topic_dir / "resume_flow.pdf"
    pdf_output_path = output_dir / "Tema1"
    temp_chunk_dir = pdf_output_path / ".temp_chunks"
    chunk_paths = [
        temp_chunk_dir / "resume_flow_chunk_001.pdf",
        temp_chunk_dir / "resume_flow_chunk_002.pdf",
    ]

    topic_dir.mkdir(parents=True)
    output_dir.mkdir()
    _write_pdf(pdf_path, pages=2)

    for chunk_path in chunk_paths:
        _write_pdf(chunk_path, pages=1)

    return {
        "input_dir": input_dir,
        "output_dir": output_dir,
        "pdf_path": pdf_path,
        "pdf_output_path": pdf_output_path,
        "chunk_1": chunk_paths[0],
        "chunk_2": chunk_paths[1],
    }


def _write_pdf(path: Path, *, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as file_obj:
        writer.write(file_obj)


def _build_use_case(
    generator: FlashcardGeneratorPort,
    repository: FileSystemChunkStateRepository,
    chunk_paths: list[Path],
) -> GenerateFlashcardsUseCase:
    use_case = GenerateFlashcardsUseCase(
        generator=generator,
        chunk_state_repository=repository,
    )
    use_case.pdf_chunker.needs_chunking = MagicMock(return_value=True)
    use_case.pdf_chunker.chunk_pdf = MagicMock(return_value=iter(chunk_paths))
    return use_case


def _build_request(
    input_dir: Path,
    output_dir: Path,
    *,
    resume: bool,
) -> GenerateFlashcardsRequest:
    return GenerateFlashcardsRequest(
        input_dir=input_dir,
        output_dir=output_dir,
        resume=resume,
    )


def _chunk_cards(front: str, back: str) -> list[Flashcard]:
    return [
        Flashcard(
            front=front,
            back=back,
        )
    ]


def _read_csv_rows(csv_path: Path) -> list[list[str]]:
    with csv_path.open(encoding="utf-8", newline="") as file_obj:
        return list(csv.reader(file_obj))


def _build_manifest(
    *,
    use_case: GenerateFlashcardsUseCase,
    pdf_path: Path,
    deck_name: str,
    total_chunks: int,
    source_signature: str,
    chunks: list[ChunkState],
) -> ChunkResumeManifest:
    now = datetime.now(UTC)
    return ChunkResumeManifest(
        source_pdf=str(pdf_path),
        source_signature=source_signature,
        deck_name=deck_name,
        total_chunks=total_chunks,
        chunks=chunks,
        created_at=now,
        updated_at=now,
    )


def _save_chunk_result(
    repository: FileSystemChunkStateRepository,
    path: Path,
    name: str,
    cards: list[Flashcard],
) -> None:
    repository.save_chunk_result(
        path,
        Deck(
            name=name,
            description=name,
            flashcards=cards,
            created_at=datetime.now(UTC),
        ),
    )


def test_resume_after_interruption_processes_only_missing_chunks(
    resume_paths: dict[str, Path],
) -> None:
    repository = FileSystemChunkStateRepository()
    chunk_paths = [resume_paths["chunk_1"], resume_paths["chunk_2"]]
    chunk_one_cards = _chunk_cards(
        "The {{c1::OSI model}} separates network communication into seven "
        "distinct abstraction layers.",
        "Each layer isolates responsibilities from physical transport up to "
        "user-facing protocols.",
    )
    chunk_two_cards = _chunk_cards(
        "A {{c1::database transaction}} applies multiple writes as one atomic "
        "unit of work.",
        "Transactions preserve consistency by committing all changes or rolling "
        "everything back.",
    )
    request = _build_request(
        resume_paths["input_dir"],
        resume_paths["output_dir"],
        resume=True,
    )
    csv_path = resume_paths["pdf_output_path"] / "resume_flow.csv"

    first_run_generator = ScriptedChunkGenerator({
        resume_paths["chunk_1"].name: chunk_one_cards,
        resume_paths["chunk_2"].name: RuntimeError("simulated interruption"),
    })
    first_run_use_case = _build_use_case(
        first_run_generator,
        repository,
        chunk_paths,
    )

    first_result = first_run_use_case.execute(request)
    state_path = first_run_use_case._get_state_file_path(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    )
    saved_manifest = repository.load_manifest(state_path)

    assert first_result == []
    assert saved_manifest is not None
    assert saved_manifest.total_chunks == 2
    assert [chunk.chunk_index for chunk in saved_manifest.chunks] == [1, 2]
    assert saved_manifest.chunks[0].status == ChunkStatus.COMPLETED
    assert saved_manifest.chunks[1].status == ChunkStatus.FAILED
    assert saved_manifest.chunks[1].error_message == "simulated interruption"
    assert saved_manifest.chunks[0].result_path is not None
    assert Path(saved_manifest.chunks[0].result_path).exists()
    assert csv_path.exists() is False

    second_run_generator = ScriptedChunkGenerator({
        resume_paths["chunk_2"].name: chunk_two_cards
    })
    second_run_use_case = _build_use_case(
        second_run_generator,
        repository,
        chunk_paths,
    )

    resumed_decks = second_run_use_case.execute(request)

    assert len(resumed_decks) == 1
    assert second_run_generator.processed_source_names == [
        resume_paths["chunk_2"].name
    ]
    assert csv_path.exists()
    assert _read_csv_rows(csv_path) == [
        [chunk_one_cards[0].front, chunk_one_cards[0].back],
        [chunk_two_cards[0].front, chunk_two_cards[0].back],
    ]
    assert not state_path.exists()
    assert not second_run_use_case._get_resume_dir(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    ).exists()
    assert not resume_paths["chunk_1"].exists()
    assert not resume_paths["chunk_2"].exists()


def test_resume_restarts_from_scratch_when_source_signature_changes(
    resume_paths: dict[str, Path],
) -> None:
    repository = FileSystemChunkStateRepository()
    chunk_paths = [resume_paths["chunk_1"], resume_paths["chunk_2"]]
    request = _build_request(
        resume_paths["input_dir"],
        resume_paths["output_dir"],
        resume=True,
    )
    initial_use_case = _build_use_case(
        ScriptedChunkGenerator({}),
        repository,
        chunk_paths,
    )
    state_path = initial_use_case._get_state_file_path(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    )
    resume_dir = initial_use_case._get_resume_dir(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    )
    saved_chunk_path = initial_use_case._get_chunk_result_path(resume_dir, 1)

    _save_chunk_result(
        repository,
        saved_chunk_path,
        "old-chunk-1",
        _chunk_cards(
            "A {{c1::stale checkpoint}} refers to persisted work from an old "
            "document signature.",
            "Resume metadata must be discarded when the source content no longer "
            "matches the saved manifest.",
        ),
    )
    stale_manifest = _build_manifest(
        use_case=initial_use_case,
        pdf_path=resume_paths["pdf_path"],
        deck_name="Tema1_resume_flow",
        total_chunks=2,
        source_signature="stale-signature",
        chunks=[
            ChunkState(
                chunk_index=1,
                status=ChunkStatus.COMPLETED,
                card_count=1,
                result_path=str(saved_chunk_path),
                updated_at=datetime.now(UTC),
            )
        ],
    )
    repository.save_manifest(state_path, stale_manifest)

    resume_paths["pdf_path"].write_bytes(
        resume_paths["pdf_path"].read_bytes() + b"\n%updated-source-signature"
    )

    fresh_run_generator = ScriptedChunkGenerator({
        resume_paths["chunk_1"].name: _chunk_cards(
            "A {{c1::write-ahead log}} records database changes before they "
            "reach the main data files.",
            "WAL entries support crash recovery and durable commit semantics.",
        ),
        resume_paths["chunk_2"].name: _chunk_cards(
            "{{c1::Vector clocks}} capture causal ordering across distributed "
            "events.",
            "Each node advances its counter so concurrent updates can be "
            "detected without a shared global clock.",
        ),
    })
    fresh_use_case = _build_use_case(
        fresh_run_generator, repository, chunk_paths
    )

    decks = fresh_use_case.execute(request)

    assert len(decks) == 1
    assert fresh_run_generator.processed_source_names == [
        resume_paths["chunk_1"].name,
        resume_paths["chunk_2"].name,
    ]
    assert not state_path.exists()
    assert not resume_dir.exists()


def test_resume_disabled_uses_normal_processing_without_state_tracking(
    resume_paths: dict[str, Path],
) -> None:
    repository = FileSystemChunkStateRepository()
    chunk_paths = [resume_paths["chunk_1"], resume_paths["chunk_2"]]
    request = _build_request(
        resume_paths["input_dir"],
        resume_paths["output_dir"],
        resume=False,
    )
    generator = ScriptedChunkGenerator({
        resume_paths["chunk_1"].name: _chunk_cards(
            "The {{c1::producer}} in Kafka appends records to a partitioned log.",
            "Partitioning preserves record order within a shard while allowing "
            "parallel throughput.",
        ),
        resume_paths["chunk_2"].name: _chunk_cards(
            "A {{c1::consumer group}} spreads topic partitions across multiple "
            "workers.",
            "Only one consumer in the group reads a given partition at a time.",
        ),
    })
    use_case = _build_use_case(generator, repository, chunk_paths)

    decks = use_case.execute(request)
    state_path = use_case._get_state_file_path(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    )

    assert len(decks) == 1
    assert generator.processed_source_names == [
        resume_paths["chunk_1"].name,
        resume_paths["chunk_2"].name,
    ]
    assert not state_path.exists()
    assert not use_case._get_resume_dir(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    ).exists()
    assert not resume_paths["chunk_1"].exists()
    assert not resume_paths["chunk_2"].exists()


def test_resume_cleans_up_when_all_chunks_are_already_complete(
    resume_paths: dict[str, Path],
) -> None:
    repository = FileSystemChunkStateRepository()
    chunk_paths = [resume_paths["chunk_1"], resume_paths["chunk_2"]]
    request = _build_request(
        resume_paths["input_dir"],
        resume_paths["output_dir"],
        resume=True,
    )
    use_case = _build_use_case(
        ScriptedChunkGenerator({}), repository, chunk_paths
    )
    state_path = use_case._get_state_file_path(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    )
    resume_dir = use_case._get_resume_dir(
        resume_paths["pdf_output_path"],
        resume_paths["pdf_path"].stem,
    )
    chunk_one_result = use_case._get_chunk_result_path(resume_dir, 1)
    chunk_two_result = use_case._get_chunk_result_path(resume_dir, 2)

    _save_chunk_result(
        repository,
        chunk_one_result,
        "chunk-1",
        _chunk_cards(
            "A {{c1::covering index}} satisfies a query without touching the base "
            "table pages.",
            "The index already contains every selected column needed by the query.",
        ),
    )
    _save_chunk_result(
        repository,
        chunk_two_result,
        "chunk-2",
        _chunk_cards(
            "{{c1::Snapshot isolation}} lets readers observe a consistent view of "
            "committed rows.",
            "Readers avoid blocking writers because they operate on versioned data.",
        ),
    )
    repository.save_manifest(
        state_path,
        _build_manifest(
            use_case=use_case,
            pdf_path=resume_paths["pdf_path"],
            deck_name="Tema1_resume_flow",
            total_chunks=2,
            source_signature=use_case._compute_source_signature(
                resume_paths["pdf_path"]
            ),
            chunks=[
                ChunkState(
                    chunk_index=1,
                    status=ChunkStatus.COMPLETED,
                    card_count=1,
                    result_path=str(chunk_one_result),
                    updated_at=datetime.now(UTC),
                ),
                ChunkState(
                    chunk_index=2,
                    status=ChunkStatus.COMPLETED,
                    card_count=1,
                    result_path=str(chunk_two_result),
                    updated_at=datetime.now(UTC),
                ),
            ],
        ),
    )

    generator = ScriptedChunkGenerator({})
    completed_use_case = _build_use_case(generator, repository, chunk_paths)

    decks = completed_use_case.execute(request)

    assert len(decks) == 1
    assert generator.processed_source_names == []
    assert not state_path.exists()
    assert not resume_dir.exists()
    assert not resume_paths["chunk_1"].exists()
    assert not resume_paths["chunk_2"].exists()
