import sys
from types import SimpleNamespace

import pytest

from flashcards_generator.integrations import (
    semantic_analysis as semantic_analysis_module,
)
from flashcards_generator.integrations import (
    semantic_chunker as semantic_chunker_module,
)
from flashcards_generator.integrations.semantic_chunker import (
    SemanticChunker,
    TextSegment,
)


def _word_count(text: str) -> int:
    return len(text.split())


def test_extract_text_skips_none_page_without_losing_later_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class Page:
        def __init__(self, text: str | None) -> None:
            self.text = text

        def extract_text(self) -> str | None:
            return self.text

    reader = SimpleNamespace(pages=[Page(None), Page("retained page")])
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=lambda *_args, **_kwargs: reader),
    )
    chunker = SemanticChunker()

    assert chunker.extract_text_from_pdf(tmp_path / "sample.pdf") == [
        TextSegment(
            "retained page",
            2,
            2,
            chunker.token_counter.count("retained page"),
        )
    ]


def test_extract_text_rejects_oversized_pdf_before_reader(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    pdf_path = tmp_path / "oversized.pdf"
    pdf_path.write_bytes(b"012345")
    monkeypatch.setattr(
        SemanticChunker, "MAX_PDF_FILE_BYTES", 5, raising=False
    )

    def fail_if_reader_called(*_args: object, **_kwargs: object) -> None:
        pytest.fail("oversized PDF reached pypdf")

    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=fail_if_reader_called),
    )

    assert SemanticChunker().extract_text_from_pdf(pdf_path) == []


def test_short_and_oversized_text_is_preserved_within_max_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    tokens = [f"sentinel_{index}" for index in range(22)]
    chunker = SemanticChunker(min_tokens=200, max_tokens=10, overlap_tokens=0)
    monkeypatch.setattr(chunker.token_counter, "count", _word_count)
    monkeypatch.setattr(
        chunker,
        "extract_text_from_pdf",
        lambda _path: [TextSegment(" ".join(tokens), 1, 1, len(tokens))],
    )
    monkeypatch.setattr(
        chunker, "find_semantic_boundaries", lambda _segments: []
    )

    chunks = list(chunker.create_semantic_chunks(tmp_path / "sample.pdf"))

    assert [token for text, _, _ in chunks for token in text.split()] == tokens
    assert all(
        chunker.token_counter.count(text) <= 10 for text, _, _ in chunks
    )


def test_boundary_chunk_starts_at_first_represented_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    chunker = SemanticChunker(target_tokens=2, min_tokens=1, max_tokens=10)
    monkeypatch.setattr(chunker.token_counter, "count", _word_count)
    monkeypatch.setattr(
        chunker,
        "extract_text_from_pdf",
        lambda _path: [
            TextSegment("one.", 1, 1, 1),
            TextSegment("two.", 2, 2, 1),
            TextSegment("three.", 3, 3, 1),
        ],
    )
    monkeypatch.setattr(
        chunker, "find_semantic_boundaries", lambda _segments: [1]
    )

    chunks = list(chunker.create_semantic_chunks(tmp_path / "sample.pdf"))

    assert [(start, end) for _, start, end in chunks] == [(1, 2), (3, 3)]


def test_overlap_chunk_metadata_includes_the_overlapped_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    chunker = SemanticChunker(min_tokens=1, max_tokens=3, overlap_tokens=1)
    monkeypatch.setattr(chunker.token_counter, "count", _word_count)
    monkeypatch.setattr(
        chunker,
        "extract_text_from_pdf",
        lambda _path: [
            TextSegment("Alpha. Beta. Gamma.", 1, 1, 3),
            TextSegment("Delta.", 2, 2, 1),
        ],
    )
    monkeypatch.setattr(
        chunker, "find_semantic_boundaries", lambda _segments: []
    )

    chunks = list(chunker.create_semantic_chunks(tmp_path / "sample.pdf"))

    assert chunks == [
        ("Alpha. Beta. Gamma.", 1, 1),
        ("Gamma. Delta.", 1, 2),
    ]
    assert all(chunker.token_counter.count(text) <= 3 for text, _, _ in chunks)


def test_chunk_creation_logs_page_range_and_token_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    chunker = SemanticChunker(min_tokens=1, max_tokens=10)
    monkeypatch.setattr(chunker.token_counter, "count", _word_count)
    monkeypatch.setattr(
        chunker,
        "extract_text_from_pdf",
        lambda _path: [TextSegment("A clear sentence.", 2, 2, 3)],
    )
    monkeypatch.setattr(
        chunker, "find_semantic_boundaries", lambda _segments: []
    )
    messages: list[str] = []
    monkeypatch.setattr(
        semantic_chunker_module,
        "logger",
        SimpleNamespace(info=messages.append),
    )

    assert list(chunker.create_semantic_chunks(tmp_path / "sample.pdf")) == [
        ("A clear sentence.", 2, 2)
    ]
    assert messages == ["Created semantic chunk 1/1: pages 2-2, 3 tokens"]


def test_pdf_reader_stream_is_closed_when_close_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class Stream:
        closed = False

        def close(self) -> None:
            self.closed = True
            raise OSError("close failed")

    stream = Stream()
    reader = SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: "retained text")],
        stream=stream,
    )
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=lambda *_args, **_kwargs: reader),
    )

    assert SemanticChunker().extract_text_from_pdf(tmp_path / "sample.pdf")
    assert stream.closed


def test_pdf_page_limit_rejects_before_extracting_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(SemanticChunker, "MAX_PDF_PAGES", 1, raising=False)
    reader = SimpleNamespace(pages=[object(), object()])
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=lambda *_args, **_kwargs: reader),
    )

    assert (
        SemanticChunker().extract_text_from_pdf(tmp_path / "sample.pdf") == []
    )


def test_pdf_page_text_limit_rejects_excessive_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        SemanticChunker, "MAX_PDF_PAGE_TEXT_CHARS", 3, raising=False
    )
    reader = SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: "too long")]
    )
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=lambda *_args, **_kwargs: reader),
    )

    assert (
        SemanticChunker().extract_text_from_pdf(tmp_path / "sample.pdf") == []
    )


def test_semantic_vectorizer_failure_uses_fixed_boundaries_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunker = SemanticChunker()
    messages: list[str] = []

    def fail_fit_transform(_vectorizer: object, _texts: list[str]) -> None:
        raise RuntimeError("vectorizer unavailable")

    monkeypatch.setattr(
        semantic_analysis_module.TfidfVectorizer,
        "fit_transform",
        fail_fit_transform,
    )
    monkeypatch.setattr(
        semantic_analysis_module,
        "logger",
        SimpleNamespace(warning=messages.append),
    )

    boundaries = chunker.find_semantic_boundaries([
        TextSegment("first distinct text", 1, 1, 3),
        TextSegment("second distinct text", 2, 2, 3),
        TextSegment("third distinct text", 3, 3, 3),
    ])

    assert boundaries == [1, 2]
    assert messages == [
        "Semantic analysis failed: vectorizer unavailable, using fixed intervals"
    ]


def test_overflow_drops_oldest_overlap_until_chunk_fits(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    def non_additive_count(text: str) -> int:
        return (
            4
            if text in {"one. two. three.", "two. three."}
            else len(text.split())
        )

    chunker = SemanticChunker(min_tokens=1, max_tokens=3, overlap_tokens=3)
    monkeypatch.setattr(chunker.token_counter, "count", non_additive_count)
    monkeypatch.setattr(
        chunker,
        "extract_text_from_pdf",
        lambda _path: [
            TextSegment("one.", 1, 1, 1),
            TextSegment("two.", 1, 1, 1),
            TextSegment("three.", 2, 2, 1),
        ],
    )
    monkeypatch.setattr(
        chunker, "find_semantic_boundaries", lambda _segments: []
    )

    assert list(chunker.create_semantic_chunks(tmp_path / "sample.pdf")) == [
        ("one. two.", 1, 1),
        ("three.", 2, 2),
    ]
