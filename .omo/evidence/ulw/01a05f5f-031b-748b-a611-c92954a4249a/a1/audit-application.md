# Application generation and export audit

**Scope and method.** Static audit of only the requested application DTOs, generation/conversion/merge/export/math modules, and the named unit/integration tests. No code or tests were changed and tests were not run (the task permits writing only this report). Line references are to the reviewed revision.

## Findings

### A1 - Explicit-file mode bypasses the input-file safety boundary
- **Severity:** High
- **Location:** `src/flashcards_generator/application/use_cases.py:295-303` (then `:380-385`).
- **Observed mechanism:** `explicit_files` is joined to `input_dir` and accepted solely when it exists and is a file. Unlike discovery mode, it never calls `_is_safe_file_path`, which rejects symlinks, paths outside `input_dir`, unsupported suffixes, and empty files. A value such as `../../other.pdf` therefore reaches `_get_output_subdir`/`_get_deck_name`; `relative_to(input_path)` at `:381` can raise before `_process_pdf`'s exception handling.
- **User-visible impact:** A CLI/API caller can make a run abort with `ValueError`, or submit an arbitrary readable non-PDF/non-PPTX as a source. This also makes explicit selection behave differently from normal discovery.
- **Reliable RED test / mutation:** Add a unit test calling `execute()` with `explicit_files=["../outside.pdf"]`, where `outside.pdf` exists beside `input_dir`; assert it returns `[]`, does not call `create_notebook`, and does not raise. The current code raises from `relative_to`. Repeat with an in-tree `notes.txt` and assert it is not submitted; current code submits it.
- **Smallest safe fix:** In the explicit-files loop, require `self._is_safe_file_path(file_path, input_path)` before appending. Keep the existing warning path for rejected names so both selection modes share the same trusted boundary.
- **Targeted verification:** `uv run pytest tests/unit/test_use_cases.py tests/unit/test_use_cases_edge_cases.py -q`

### A2 - A missing or unreadable saved chunk result aborts resume instead of regenerating it
- **Severity:** Medium
- **Location:** `src/flashcards_generator/application/use_cases.py:485-495`.
- **Observed mechanism:** A manifest entry marked `COMPLETED` is treated as authoritative. `load_chunk_result(Path(result_path))` is called before the index is marked complete, with no recovery for a deleted/corrupt result or a path outside the resume directory. The exception escapes `_process_large_pdf`; only some exception classes are converted to `None` by `_process_pdf` at `:925-934`.
- **User-visible impact:** After cleanup, manual deletion, partial disk loss, or malformed persisted state, `--resume` cannot resume the affected chunk and may abort the whole PDF without a replacement CSV or a useful failed-chunk state.
- **Reliable RED test / mutation:** In `test_use_cases_resume.py`, save a matching manifest with chunk 1 `COMPLETED` and `result_path` pointing to a nonexistent JSON, then make chunk 2 available. Assert `execute()` processes chunk 1 anew and completes. The current path raises `FileNotFoundError` (or the repository parse error) before any chunk is processed.
- **Smallest safe fix:** Treat loading a completed result as conditional: validate the index is in `1..total_chunks` and the result lies under `resume_dir`; on load/parse failure, log the chunk and leave it out of `completed_chunk_indexes` so normal processing regenerates it. Persisting the new completed state through the existing post-processing lane keeps `ChunkStatePort` as the sole persistence dependency.
- **Targeted verification:** `uv run pytest tests/unit/test_use_cases_resume.py tests/integration/test_resume_flow.py -q`

### A3 - Background generation writes an empty CSV that permanently suppresses retry
- **Severity:** High
- **Location:** `src/flashcards_generator/application/use_cases.py:182-189, 982-989, 1057-1061`.
- **Observed mechanism:** With `wait_for_completion=False`, `_handle_artifact_completion` returns an intentionally empty, still-generating `Deck`. `execute` treats any `Deck` as complete, exports a zero-row CSV, and (when resume is enabled) cleans resume state. Later `_process_pdf` skips solely because that CSV exists at `:886-890`.
- **User-visible impact:** The user gets an empty file before the artifact is ready; rerunning silently skips the source even after completion. The logged artifact ID is not persisted in the exported result, so the normal generation flow does not provide a recoverable export path.
- **Reliable RED test / mutation:** Update `test_execute_no_wait_mode` to invoke `execute()` twice with the same request and assert the first invocation creates no `file.csv` and the second is not skipped (or, if a completed-artifact retrieval flow is added, assert that it performs that retrieval). Current test explicitly asserts the incorrect empty CSV exists and the second call would skip it.
- **Smallest safe fix:** Keep returning the background `Deck` for caller visibility, but gate `_save_deck` and resume cleanup on `request.wait_for_completion`; only a completed artifact may create the CSV completion marker. This preserves the current port API and prevents the irreversible skip. A follow-up artifact-retrieval command can be designed separately.
- **Targeted verification:** `uv run pytest tests/unit/test_use_cases.py -q`

### A4 - Duplicate cards are exported for non-chunked documents
- **Severity:** Medium
- **Location:** `src/flashcards_generator/application/use_cases.py:596-625, 1034-1055`.
- **Observed mechanism:** The large-PDF path calls `combined_deck.deduplicate(...)` and quality filtering, but `_download_and_convert` creates and returns the ordinary-document deck without either step. Export at `:1057-1061` writes every returned card.
- **User-visible impact:** Identical model output from a normal PDF becomes repeated rows in Anki/CSV, contrary to the generation instructions and unlike the chunked path.
- **Reliable RED test / mutation:** Have the generator parse two identical valid flashcards for a non-chunked PDF, execute it, and read the CSV with `csv.reader`; assert one row. Current behavior writes two. Include duplicate fronts/backs with enough content to survive conversion, not mocked converter output, so the test exercises the conversion-to-export lane.
- **Smallest safe fix:** Apply the same existing deck-level deduplication once after the normal deck is assembled and before it is returned for export. Use the existing threshold/semantics from the chunked lane; do not introduce a separate duplicate algorithm.
- **Targeted verification:** `uv run pytest tests/unit/test_use_cases.py -q`

### A5 - Converter accepts cards with no valid cloze marker
- **Severity:** Medium
- **Location:** `src/flashcards_generator/application/converter.py:230-242, 245-260, 332-353`.
- **Observed mechanism:** `_is_quality_valid` rejects short strings and trivial *matched* clozes, but never requires a match. For a long answer with more than five lowercase, non-keyword words, `_create_multi_cloze` returns plain text. Likewise, malformed pre-existing `{{c...` text takes the fast path and passes when the regex finds no valid `{{cN::...}}` marker.
- **User-visible impact:** A purported Cloze deck can export ordinary text or malformed card syntax, producing unusable/revealing cards on import rather than rejecting invalid model output.
- **Reliable RED test / mutation:** Add `assert ClozeConverter().convert(Flashcard(front="Explain this", back="alpha beta gamma delta epsilon zeta")) is None`; current code returns a `Flashcard` whose front contains no `{{c`. Also assert malformed `front="Text {{cX::answer}}"` is rejected; current code returns it.
- **Smallest safe fix:** In `_is_quality_valid`, return `False` when `CLOZE_PATTERN.findall(cloze_text)` is empty, before the existing per-match checks. This retains all accepted valid clozes and makes the fast path conform to the stated format.
- **Targeted verification:** `uv run pytest tests/unit/test_converter.py -q`

### A6 - CSV merge silently truncates malformed rows with extra columns
- **Severity:** Medium
- **Location:** `src/flashcards_generator/application/csv_merger.py:58-74`.
- **Observed mechanism:** The comment says 2-column validation, but the guard is only `len(row) < 2`; rows with three or more columns are accepted and `writer.writerow([row[0], row[1]])` discards every extra field without an error or log.
- **User-visible impact:** Tags, notes, or accidentally split data are silently lost while the returned count reports a successful merge. This is data corruption at an import/export boundary.
- **Reliable RED test / mutation:** Merge a file containing `["front", "back", "tag"]` and assert `CSVMergeError` identifies the malformed source/row (the API promises two columns). Current code returns one and outputs only `front,back`.
- **Smallest safe fix:** Require `len(row) == 2`; raise `CSVMergeError` with the source filename and row number for any other non-empty row. Keep the existing intentional skip of short/blank rows only if that tolerance is contractual; otherwise reject those too in the same validation lane.
- **Targeted verification:** `uv run pytest tests/unit/test_csv_merger.py -q`

### A7 - Anki TSV export does not preserve two-column records containing tabs or newlines
- **Severity:** Medium
- **Location:** `src/flashcards_generator/application/exporter.py:40-54`.
- **Observed mechanism:** `export_anki` constructs each record with raw interpolation around one tab. A card front/back containing a tab creates additional TSV fields; a newline creates a new record. CSV export correctly delegates escaping to `csv.writer`, but the Anki format does no normalization.
- **User-visible impact:** Valid generated text can import as shifted cards, truncated backs, or extra cards in Anki despite the header declaring a two-column tab-separated file.
- **Reliable RED test / mutation:** Export a deck with `front="one\ttwo"` and `back="line 1\nline 2"`; parse non-comment output records and assert exactly one record with two fields. Current output has an extra field and physical line.
- **Smallest safe fix:** Normalize TSV field delimiters at this boundary before interpolation (for example, tabs to spaces and CR/LF to `<br>` because the header declares HTML), after math conversion. Apply the same helper to front and back only; leave CSV behavior unchanged.
- **Targeted verification:** `uv run pytest tests/unit/test_exporter.py -q`

### A8 - Chunk retry is narrowly typed and transient non-`RuntimeError` failures bypass retry/state reporting
- **Severity:** Medium
- **Location:** `src/flashcards_generator/application/use_cases.py:690-761, 851-859, 925-934`.
- **Observed mechanism:** `_process_chunk_internal` re-raises `GenerationError`, `SourceProcessingError`, `OSError`, and `RuntimeError`, but `_process_chunk_with_retry` catches only `RuntimeError`. An `OSError` during a transient download, for example, escapes the retry method and the large-PDF loop before its `FAILED` manifest update at `:543-557`; `_process_pdf` then reduces it to `None`.
- **User-visible impact:** A resumable chunk can stop on a transient boundary failure without the advertised retry/backoff or a persisted chunk error. The user sees only a generic processing failure and must infer which chunk is safe to resume.
- **Reliable RED test / mutation:** Stub `_process_chunk_internal` to raise `OSError("temporary download failure")` once then return a deck. Assert `_process_chunk_with_retry` calls it twice and returns the deck, with sleep monkeypatched. Current code raises on the first call. A complementary resume test should assert the final non-retryable failure creates a `FAILED` manifest entry.
- **Smallest safe fix:** Define the retryable exception policy at this application boundary and catch the relevant transient port/I/O exceptions alongside `RuntimeError`; preserve immediate propagation for cancellation (`KeyboardInterrupt`/`CancelledError`) and explicitly set `_last_chunk_error_message` before returning `None` after retries. Reuse the existing manifest-failure lane rather than adding parallel state.
- **Targeted verification:** `uv run pytest tests/unit/test_use_cases.py tests/unit/test_use_cases_resume.py tests/integration/test_resume_flow.py -q`

## Checked categories and test-gap ledger

| Category | Checked behavior / conclusion | Residual risk or missing test |
|---|---|---|
| Generation state transitions | Normal completion exports; timeout/background returns a deck; chunk completion persists per-chunk result then manifest; successful `execute` exports then removes resume state. A2 and A3 break recovery/completion semantics. | No test simulates exporter failure between a successful chunk deck and resume cleanup. |
| Resume | Matching source signature and chunk count reuse completed results; stale signature restarts; successful runs clean state (`test_use_cases_resume.py`, `test_resume_flow.py`). | No missing/corrupt/out-of-range persisted result test (A2). Source signature uses size/mtime/path at `use_cases.py:230-233`, so same-size edits with preserved mtime are not detected. |
| Retry and performance | Retry backs off 5/10 seconds for selected `RuntimeError`s; every pair of chunks also unconditionally sleeps five seconds at `use_cases.py:590-594`. | Tests monkeypatch sleep rather than asserting retry classification/delay. Long documents incur at least `5*(chunks-1)` seconds of serial delay even with no rate limit; A8 covers correctness, while adaptive pacing is a separate performance decision. |
| Cancellation | `KeyboardInterrupt` is not caught by the listed `except Exception`-style paths, and `finally` blocks at `use_cases.py:192-193` and `:635-637` run cleanup. | No test asserts cancellation preserves prior completed manifest entries and does not schedule another chunk; add one using a second-chunk `KeyboardInterrupt` and no time-based waits. |
| Empty input / empty output | No discovered PDFs returns `[]` (`test_execute_no_pdfs`). Chunking with no generated cards returns no deck before export. | No test covers all cards being rejected by conversion/quality filtering; a completed empty deck can still be written in the ordinary/background paths (A3). |
| Duplicate cards | Chunked combined decks deduplicate before quality filtering. | Normal generation lacks the equivalent test and behavior (A4). CSV merger deduplication tests only exact two-column rows. |
| CSV correctness | CSV exporter uses quoted two-column rows; merger excludes its output and reads quoted rows. | Extra columns are silently discarded (A6); exporter test counts quote separators rather than parsing embedded commas/newlines with `csv.reader`. |
| Exception propagation and observability | Merge translates exceptions to `CSVMergeError`; generation logs source/chunk errors; `_process_pdf` intentionally converts several errors to `None`. Cancellation is not swallowed. | Resume load failure is not normalized/reported as chunk state (A2); A8's non-`RuntimeError` transient path skips retry state. Logs are the only caller-visible failure detail because `execute` returns an empty/partial deck list. |
| Math conversion | Dollar notation is converted before CSV/Anki export and tested for basic inline/display cases. | No test covers dollars inside code/text or multiline display math; not a generation/export state blocker identified by this audit. |

## Verification of this report

Re-read after writing: each of A1-A8 contains a severity, exact `file:line`, concrete mechanism, user impact, falsifiable RED test/mutation, smallest dependency-aware fix lane, and a targeted command. Resume (A2), retry (A8), cancellation (ledger), empty input/output (ledger), duplicate cards (A4), CSV correctness (A6-A7), and exception propagation (A2/A8 and ledger) are explicitly addressed.
