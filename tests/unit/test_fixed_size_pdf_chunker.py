from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from flashcards_generator.integrations.fixed_size_pdf_chunker import (
    FixedSizePDFChunkWriter,
)


@pytest.mark.parametrize(
    ("total_pages", "expected_ranges"),
    [
        (0, []),
        (1, [(0, 1)]),
        (30, [(0, 30)]),
        (31, [(0, 30), (25, 31)]),
        (51, [(0, 30), (25, 51)]),
        (56, [(0, 30), (25, 55), (50, 56)]),
    ],
)
def test_fixed_size_chunks_preserve_ranges_and_names(
    tmp_path: Path,
    total_pages: int,
    expected_ranges: list[tuple[int, int]],
) -> None:
    reader = Mock(pages=list(range(total_pages)))
    writers = [Mock() for _ in expected_ranges]
    source = tmp_path / "source.pdf"

    with patch("pypdf.PdfWriter", side_effect=writers):
        chunks = list(
            FixedSizePDFChunkWriter(30, 5).write(
                reader, source, tmp_path / "output"
            )
        )

    assert [
        [call.args[0] for call in writer.add_page.call_args_list]
        for writer in writers
    ] == [list(range(start, end)) for start, end in expected_ranges]
    assert [chunk.name for chunk in chunks] == [
        f"source_chunk_{index:03d}.pdf"
        for index in range(1, len(expected_ranges) + 1)
    ]
