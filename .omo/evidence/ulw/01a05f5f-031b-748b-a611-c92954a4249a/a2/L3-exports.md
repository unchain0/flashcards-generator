# L3 conversion, merge, and export verification

## Scope and contract

Re-read before implementation:

- `a1/audit-synthesis.md` L3/B11-B13 map.
- `a1/audit-domain-dto.md` finding 3 (TSV delimiter integrity).
- `a1/audit-application.md` A5-A7.

Production changes are limited to `application/converter.py`,
`application/csv_merger.py`, and `application/exporter.py`. Regression tests
are limited to their corresponding unit-test modules.

Selected behavior:

- A converted card must contain at least one valid `{{cN::...}}` marker.
- Blank/short legacy merge rows remain skipped, but a row with more than two
  columns raises `CSVMergeError` naming its source and logical row.
- CSV and Anki TSV use the standard-library CSV writer. TSV uses a tab
  delimiter and `QUOTE_ALL`, so tabs and newlines remain part of the two
  logical fields when parsed as TSV. Existing Anki headers, UTF-8 encoding,
  and math conversion calls are retained.

## RED transcripts

### Invalid or no-cloze cards

Command:

```console
$ uv run pytest tests/unit/test_converter.py::TestClozeConverter::test_convert_rejects_cards_without_valid_cloze -q
```

Output:

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 2 items

tests/unit/test_converter.py FF                                          [100%]

=================================== FAILURES ===================================
_ TestClozeConverter.test_convert_rejects_cards_without_valid_cloze[Explain this-alpha beta gamma delta epsilon zeta] _
tests/unit/test_converter.py:186: in test_convert_rejects_cards_without_valid_cloze
    assert result is None
E   AssertionError: assert Flashcard(front='alpha beta gamma delta epsilon zeta', back='alpha beta gamma delta epsilon zeta', tags=[], source='') is None
_ TestClozeConverter.test_convert_rejects_cards_without_valid_cloze[Text {{cX::answer}}-answer] _
tests/unit/test_converter.py:186: in test_convert_rejects_cards_without_valid_cloze
    assert result is None
E   AssertionError: assert Flashcard(front='Text {{cX::answer}}', back='answer', tags=[], source='') is None
=========================== short test summary info ============================
FAILED tests/unit/test_converter.py::TestClozeConverter::test_convert_rejects_cards_without_valid_cloze[Explain this-alpha beta gamma delta epsilon zeta]
FAILED tests/unit/test_converter.py::TestClozeConverter::test_convert_rejects_cards_without_valid_cloze[Text {{cX::answer}}-answer]
============================== 2 failed in 0.10s ===============================
```

### Extra-column merge row

Command:

```console
$ uv run pytest tests/unit/test_csv_merger.py::TestCsvMerger::test_merge_rejects_rows_with_extra_columns -q
```

Output:

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 1 item

tests/unit/test_csv_merger.py F                                          [100%]

=================================== FAILURES ===================================
___________ TestCsvMerger.test_merge_rejects_rows_with_extra_columns ___________
tests/unit/test_csv_merger.py:132: in test_merge_rejects_rows_with_extra_columns
    with pytest.raises(CSVMergeError) as exc_info:
E   Failed: DID NOT RAISE <class 'flashcards_generator.domain.exceptions.CSVMergeError'>
=========================== short test summary info ============================
FAILED tests/unit/test_csv_merger.py::TestCsvMerger::test_merge_rejects_rows_with_extra_columns
============================== 1 failed in 0.05s ===============================
```

### Tab/newline field delimiters

Command:

```console
$ uv run pytest tests/unit/test_exporter.py::TestDeckExporter::test_exporters_preserve_delimited_fields_as_two_columns -q
```

Output:

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 1 item

tests/unit/test_exporter.py F                                            [100%]

=================================== FAILURES ===================================
___ TestDeckExporter.test_exporters_preserve_delimited_fields_as_two_columns ___
tests/unit/test_exporter.py:64: in test_exporters_preserve_delimited_fields_as_two_columns
    assert [row for row in anki_rows if len(row) == 2] == [[front, back]]
E   AssertionError: assert [] == [['Question\t...swer line 2']]
E     
E     Right contains one more item: ['Question\tcontinued', 'Answer line 1\nAnswer line 2']
E     Use -v to get more diff
=========================== short test summary info ============================
FAILED tests/unit/test_exporter.py::TestDeckExporter::test_exporters_preserve_delimited_fields_as_two_columns
============================== 1 failed in 0.04s ===============================
```

## Implementation

- `ClozeConverter._is_quality_valid` now rejects text with no valid cloze
  regex matches before applying the existing quality checks.
- `CsvMerger.merge` enumerates logical CSV records, rejects rows with extra
  fields as a contextual `CSVMergeError`, and allows that error to retain its
  source path rather than wrapping it with the folder path. Its quoted,
  two-column input/output path remains CSV-reader/writer based.
- `DeckExporter.export_anki` writes unchanged headers followed by
  `csv.writer(..., delimiter="\t", quoting=csv.QUOTE_ALL)`. This preserves
  embedded tabs and newlines as quoted TSV fields instead of treating them as
  record or field delimiters.

## GREEN transcripts

### Focused regressions

```text
$ uv run pytest tests/unit/test_converter.py::TestClozeConverter::test_convert_rejects_cards_without_valid_cloze -q
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 2 items

tests/unit/test_converter.py ..                                          [100%]

============================== 2 passed in 0.02s ===============================

$ uv run pytest tests/unit/test_csv_merger.py::TestCsvMerger::test_merge_rejects_rows_with_extra_columns tests/unit/test_csv_merger.py::TestCsvMerger::test_merge_preserves_quoted_two_column_rows -q
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 2 items

tests/unit/test_csv_merger.py ..                                         [100%]

============================== 2 passed in 0.02s ===============================

$ uv run pytest tests/unit/test_exporter.py::TestDeckExporter::test_exporters_preserve_delimited_fields_as_two_columns -q
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 1 item

tests/unit/test_exporter.py .                                            [100%]

============================== 1 passed in 0.02s ===============================
```

The quoted merge regression parses the merged file with `csv.reader` and
asserts the exact comma/tab/newline/quote-containing pair. The exporter
regression parses both generated files (`csv.reader` and tab-delimited
`csv.reader`) and asserts the exact two-field pair. The normal Anki regression
also parser-checks the retained deck, separator, and HTML header records.

### Scoped suite

```text
$ uv run pytest tests/unit/test_converter.py tests/unit/test_csv_merger.py tests/unit/test_exporter.py -q
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 50 items

tests/unit/test_converter.py ..................................          [ 68%]
tests/unit/test_csv_merger.py ...........                                [ 90%]
tests/unit/test_exporter.py .....                                        [100%]

============================== 50 passed in 0.08s ==============================
```

### Diagnostics

`lsp_diagnostics` was run after the final production change for each changed
production module:

```text
src/flashcards_generator/application/converter.py: No diagnostics found
src/flashcards_generator/application/csv_merger.py: No diagnostics found
src/flashcards_generator/application/exporter.py: No diagnostics found
```

`git diff --check` over the six lane source/test files completed with no
output. No commit was created.
