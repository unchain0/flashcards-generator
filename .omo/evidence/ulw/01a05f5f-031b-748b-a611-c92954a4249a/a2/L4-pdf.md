# L4 PDF/PPTX lane

## Scope and implementation

Implemented only `src/flashcards_generator/infrastructure/pdf_utils.py`; regression coverage was added only to `tests/unit/test_pdf_utils.py` and `tests/unit/test_pptx_converter.py`.

- `PDFChunker` rejects `chunk_size <= 0` and overlap outside `[0, chunk_size)`.
- Fixed chunks use the single stride `chunk_size - overlap_pages`; 51 pages at `(30, 5)` now writes exactly source ranges `[0, 30)` and `[25, 51)`.
- Chapter chunking prepends the unbookmarked leading prefix to the first chunk and tracks its actual end source page.
- `PdfReadError` (including `EmptyFileError`) is a controlled fallback for counting, outline extraction, and chunk generation.
- Fixed and chapter generator readers close in `finally`, including an explicitly closed partially-consumed generator.
- PPTX conversion uses a per-call temporary directory, requires a fresh regular PDF there, and atomically moves it to the requested output only after validation. Missing LibreOffice remains a `None` result.

No semantic chunking or logging behavior was changed.

## RED evidence

Command:

```console
uv run pytest tests/unit/test_pdf_utils.py -q -k 'rejects_invalid_chunk_configuration or fixed_size_ranges_use_single_overlap_and_close_reader'
```

Failure summary:

```text
collected 24 items / 22 deselected / 2 selected

tests/unit/test_pdf_utils.py FF

=================================== FAILURES ===================================
___________ TestPDFChunker.test_rejects_invalid_chunk_configuration ____________
tests/unit/test_pdf_utils.py:214: in test_rejects_invalid_chunk_configuration
    with pytest.raises(ValueError, match="chunk_size"):
E   Failed: DID NOT RAISE <class 'ValueError'>
__ TestPDFChunker.test_fixed_size_ranges_use_single_overlap_and_close_reader ___
tests/unit/test_pdf_utils.py:245: in test_fixed_size_ranges_use_single_overlap_and_close_reader
    assert [
E   assert [[0, 1, 2, 3,..., 48, 49, 50]] == [[0, 1, 2, 3,... 29, 30, ...]]
E
E     At index 1 diff: [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49] != [25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50]
E     Left contains one more item: [45, 46, 47, 48, 49, 50]
=========================== short test summary info ============================
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_rejects_invalid_chunk_configuration
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_fixed_size_ranges_use_single_overlap_and_close_reader
======================= 2 failed, 22 deselected in 0.16s =======================
```

Command:

```console
uv run pytest tests/unit/test_pdf_utils.py tests/unit/test_pptx_converter.py -q -k 'chapter_chunk_preserves_leading_pages or corrupt_pdf_returns_controlled_fallbacks or convert_rejects_stale_output'
```

Failure summary:

```text
collected 34 items / 31 deselected / 3 selected

tests/unit/test_pdf_utils.py FF
tests/unit/test_pptx_converter.py F

=================================== FAILURES ===================================
__________ TestPDFChunker.test_chapter_chunk_preserves_leading_pages ___________
tests/unit/test_pdf_utils.py:268: in test_chapter_chunk_preserves_leading_pages
E   assert [5, 6, 7, 8, 9] == [0, 1, 2, 3, 4, 5, ...]
E     At index 0 diff: 5 != 0
_________ TestPDFChunker.test_corrupt_pdf_returns_controlled_fallbacks _________
tests/unit/test_pdf_utils.py:280: in test_corrupt_pdf_returns_controlled_fallbacks
    assert chunker.count_pages(pdf_path) == 0
E   pypdf.errors.EmptyFileError: empty
_____________ TestPPTXConverter.test_convert_rejects_stale_output ______________
tests/unit/test_pptx_converter.py:126: in test_convert_rejects_stale_output
    assert converter.convert(pptx_path, output_dir) is None
E   AssertionError: assert PosixPath('.../output/test.pdf') is None
=========================== short test summary info ============================
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_chapter_chunk_preserves_leading_pages
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_corrupt_pdf_returns_controlled_fallbacks
FAILED tests/unit/test_pptx_converter.py::TestPPTXConverter::test_convert_rejects_stale_output
======================= 3 failed, 31 deselected in 0.19s =======================
```

## GREEN evidence

Command:

```console
uv run pytest tests/unit/test_pdf_utils.py tests/unit/test_pptx_converter.py -q
```

Success summary:

```text
collected 35 items

tests/unit/test_pdf_utils.py .........................                   [ 71%]
tests/unit/test_pptx_converter.py ..........                             [100%]

============================== 35 passed in 0.17s ==============================
```

Additional validation:

```text
$ uv run ruff check src/flashcards_generator/infrastructure/pdf_utils.py tests/unit/test_pdf_utils.py tests/unit/test_pptx_converter.py
All checks passed!

$ LSP diagnostics: src/flashcards_generator/infrastructure/pdf_utils.py
No diagnostics found
```

## Concrete assertions

- Range test captures each `PdfWriter.add_page` argument and asserts exactly `list(range(30))` then `list(range(25, 51))`, with two outputs only.
- Chapter test captures writer page arguments and asserts `list(range(10))` for bookmark range `(5, 10)`; the leading source pages are retained.
- Full-consumption and early-generator-close tests both assert `reader.stream.close.assert_called_once_with()`.
- Corrupt-PDF test injects `EmptyFileError` and asserts `count_pages(...) == 0`, `get_chapter_boundaries(...) == []`, and `list(chunk_pdf(...)) == []`.
- Stale-output test pre-creates `output/test.pdf`, simulates a no-op successful LibreOffice call, asserts `convert(...) is None`, and asserts its sentinel content remains unchanged.

## Scope check

`git diff --name-only -- src/flashcards_generator/infrastructure/pdf_utils.py tests/unit/test_pdf_utils.py tests/unit/test_pptx_converter.py` lists exactly those three lane paths. The working tree contains concurrent/pre-existing changes outside this lane (including `pyproject.toml`, `uv.lock`, and other L1/L4 paths); they were not modified by this implementation.

## Complete command transcripts

### RED: ranges and invalid configuration

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 24 items / 22 deselected / 2 selected

tests/unit/test_pdf_utils.py FF                                          [100%]

=================================== FAILURES ===================================
___________ TestPDFChunker.test_rejects_invalid_chunk_configuration ____________
tests/unit/test_pdf_utils.py:214: in test_rejects_invalid_chunk_configuration
    with pytest.raises(ValueError, match="chunk_size"):
E   Failed: DID NOT RAISE <class 'ValueError'>
__ TestPDFChunker.test_fixed_size_ranges_use_single_overlap_and_close_reader ___
tests/unit/test_pdf_utils.py:245: in test_fixed_size_ranges_use_single_overlap_and_close_reader
    assert [
E   assert [[0, 1, 2, 3,..., 48, 49, 50]] == [[0, 1, 2, 3,... 29, 30, ...]]
E     
E     At index 1 diff: [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49] != [25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50]
E     Left contains one more item: [45, 46, 47, 48, 49, 50]
E     Use -v to get more diff
----------------------------- Captured stderr call -----------------------------
2026-09-01 21:51:25.085 | INFO     | flashcards_generator.infrastructure.pdf_utils:_chunk_fixed_size_with_overlap:425 - Splitting source.pdf (51 pages) into 3 chunks with 5 pages overlap
2026-09-01 21:51:25.086 | INFO     | flashcards_generator.infrastructure.pdf_utils:_chunk_fixed_size_with_overlap:459 - Created chunk 1/3: pages 1-30
2026-09-01 21:51:25.087 | INFO     | flashcards_generator.infrastructure.pdf_utils:_chunk_fixed_size_with_overlap:459 - Created chunk 2/3: pages 21-50 (+5 overlap)
2026-09-01 21:51:25.087 | INFO     | flashcards_generator.infrastructure.pdf_utils:_chunk_fixed_size_with_overlap:459 - Created chunk 3/3: pages 46-51 (+5 overlap)
=========================== short test summary info ============================
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_rejects_invalid_chunk_configuration
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_fixed_size_ranges_use_single_overlap_and_close_reader
======================= 2 failed, 22 deselected in 0.16s =======================
```

### RED: chapter/corrupt/PPTX boundaries

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 34 items / 31 deselected / 3 selected

tests/unit/test_pdf_utils.py FF                                          [ 66%]
tests/unit/test_pptx_converter.py F                                      [100%]

=================================== FAILURES ===================================
__________ TestPDFChunker.test_chapter_chunk_preserves_leading_pages ___________
tests/unit/test_pdf_utils.py:268: in test_chapter_chunk_preserves_leading_pages
    assert [call.args[0] for call in writer.add_page.call_args_list] == list(
E   assert [5, 6, 7, 8, 9] == [0, 1, 2, 3, 4, 5, ...]
E     
E     At index 0 diff: 5 != 0
E     Right contains 5 more items, first extra item: 5
E     Use -v to get more diff
----------------------------- Captured stderr call -----------------------------
2026-09-01 21:51:25.048 | INFO     | flashcards_generator.infrastructure.pdf_utils:_chunk_by_chapters:409 - Created chunk 1/1: pages 1-5 (chapters: Chapter)
_________ TestPDFChunker.test_corrupt_pdf_returns_controlled_fallbacks _________
tests/unit/test_pdf_utils.py:280: in test_corrupt_pdf_returns_controlled_fallbacks
    assert chunker.count_pages(pdf_path) == 0
src/flashcards_generator/infrastructure/pdf_utils.py:131: in count_pages
    reader = self._create_reader(pdf_path)
../../../.local/share/uv/python/cpython-3.10-linux-x86_64-gnu/lib/python3.10/unittest/mock.py:1114: in __call__
    return self._mock_call(*args, **kwargs)
../../../.local/share/uv/python/cpython-3.10-linux-x86_64-gnu/lib/python3.10/unittest/mock.py:1118: in _mock_call
    return self._execute_mock_call(*args, **kwargs)
../../../.local/share/uv/python/cpython-3.10-linux-x86_64-gnu/lib/python3.10/unittest/mock.py:1173: in _execute_mock_call
    raise effect
E   pypdf.errors.EmptyFileError: empty
_____________ TestPPTXConverter.test_convert_rejects_stale_output ______________
tests/unit/test_pptx_converter.py:126: in test_convert_rejects_stale_output
    assert converter.convert(pptx_path, output_dir) is None
E   AssertionError: assert PosixPath('/tmp/pytest-of-avell/pytest-496/test_convert_rejects_stale_out0/output/test.pdf') is None
E    +  where PosixPath('/tmp/pytest-of-avell/pytest-496/test_convert_rejects_stale_out0/output/test.pdf') = convert(PosixPath('/tmp/pytest-of-avell/pytest-496/test_convert_rejects_stale_out0/test.pptx'), PosixPath('/tmp/pytest-of-avell/pytest-496/test_convert_rejects_stale_out0/output'))
E    +    where convert = <flashcards_generator.infrastructure.pdf_utils.PPTXConverter object at 0x7f2b5f41c100>.convert
----------------------------- Captured stderr call -----------------------------
2026-09-01 21:51:25.142 | INFO     | flashcards_generator.infrastructure.pdf_utils:convert:77 - Converted test.pptx → test.pdf
=========================== short test summary info ============================
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_chapter_chunk_preserves_leading_pages
FAILED tests/unit/test_pdf_utils.py::TestPDFChunker::test_corrupt_pdf_returns_controlled_fallbacks
FAILED tests/unit/test_pptx_converter.py::TestPPTXConverter::test_convert_rejects_stale_output
======================= 3 failed, 31 deselected in 0.19s =======================
```

### GREEN: scoped unit suite

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 35 items

tests/unit/test_pdf_utils.py .........................                   [ 71%]
tests/unit/test_pptx_converter.py ..........                             [100%]

============================== 35 passed in 0.17s ==============================
```
