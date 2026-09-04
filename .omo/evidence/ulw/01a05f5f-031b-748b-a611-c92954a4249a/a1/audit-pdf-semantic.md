# Audit: PDF/PPTX conversion, semantic chunking, quality filtering, and logging

## Scope and confidence

Audited only:

- `src/flashcards_generator/infrastructure/pdf_utils.py`
- `src/flashcards_generator/infrastructure/semantic_chunker.py`
- `src/flashcards_generator/infrastructure/logging_config.py`
- `tests/unit/test_pdf_utils.py`
- `tests/unit/test_pptx_converter.py`
- `tests/test_semantic_chunking.py` (the requested `tests/unit/test_semantic_chunking.py` does not exist)
- `tests/unit/test_logging_config.py`

**Severity** is the first label in each finding heading. **Verified defect** means the mechanism follows directly from the code and was either exercised without modifying the repository or has a deterministic mutation test. **Verified resource risk** means the complexity/lifetime is explicit in the code, although the failure threshold depends on workload. **Hypothesis** means impact depends on lifecycle outside the allowed scope.

## Findings

### F1 — High — Fixed-size PDF chunks have incorrect overlap and an extra tail chunk (verified defect)

- **Evidence:** `src/flashcards_generator/infrastructure/pdf_utils.py:420-423` computes the count using stride `chunk_size - overlap_pages`; `:434-440` uses that stride and then subtracts overlap a second time for every chunk after the first. No constructor validation protects the divisor (`:105-112`). The only success test uses zero overlap (`tests/unit/test_pdf_utils.py:96-116`), so it cannot detect this.
- **Observed mechanism:** With defaults and 51 pages, ranges are `[0,30)`, `[20,50)`, `[45,51)`: the first overlap is 10 rather than 5 pages and a third tiny chunk is emitted. `chunk_size == overlap_pages` raises `ZeroDivisionError`; larger overlap can produce no chunks.
- **User-visible impact:** Duplicate flashcards/context, redundant model calls, misleading `(+5 overlap)` logs, tiny low-quality tail chunks, or a crash/silent no-output for invalid configuration.
- **Reliable RED test:** Mock 51 reader pages and capture `add_page` calls per writer for `PDFChunker(30, 5)`; assert exactly two ranges, `[0,30)` and `[25,51)`. It fails with three ranges above. Separately assert construction/chunking rejects `(30,30)` with `ValueError`, not division by zero.
- **Smallest safe fix:** Validate `chunk_size > 0` and `0 <= overlap_pages < chunk_size`; calculate `num_chunks = 1` when `total_pages <= chunk_size`, otherwise `1 + ceil((total_pages-chunk_size)/stride)`; use `start_page = chunk_idx * stride` without the second subtraction.
- **Verification:** `uv run pytest tests/unit/test_pdf_utils.py -q`

### F2 — High — Chapter chunking omits pre-outline pages and reports false page ranges (verified defect)

- **Evidence:** `src/flashcards_generator/infrastructure/pdf_utils.py:305` initializes metadata at page 0, while `:317` derives size solely from bookmark boundaries and `:359-360` adds pages beginning at `ch_start`. Metadata advances by the summed chapter lengths at `:366`, not by actual page indices.
- **Observed mechanism:** For a 10-page PDF whose first bookmark is `(5,10,"Chapter")`, the writer receives pages 5–9, pages 0–4 are never written, yet `actual_end_page` is 5 and the log says pages 1–5.
- **User-visible impact:** Front matter or unbookmarked instructional content disappears; logs and downstream page citations refer to the wrong pages.
- **Reliable RED test:** Invoke `_chunk_by_chapters` with ten distinguishable mocked pages and `[(5,10,"Chapter")]`; inspect the written writer. Assert all intended pages are covered and logged bounds match actual indices. Current output contains only pages 5–9 but records 0–5.
- **Smallest safe fix:** Define an explicit policy for the prefix and implement it consistently: normally prepend `[0, first_ch_start)` to the first retained chunk. Track each chunk's actual minimum/maximum source page rather than deriving bounds from accumulated lengths.
- **Verification:** `uv run pytest tests/unit/test_pdf_utils.py -q`

### F3 — High — Empty/corrupt PDFs can escape `count_pages` and outline handling (verified defect)

- **Evidence:** `src/flashcards_generator/infrastructure/pdf_utils.py:131-135` and `:153-195` catch only `OSError`, `ImportError`, and `RuntimeError`. `pypdf.errors.EmptyFileError` inherits `PdfReadError -> PyPdfError -> Exception`, not any caught class. The existing test mutates only `OSError` (`tests/unit/test_pdf_utils.py:146-155`). Direct chunk paths at `pdf_utils.py:300` and `:418` have no input-error boundary at all.
- **Observed mechanism:** A zero-byte or structurally corrupt file can raise `EmptyFileError`/`PdfReadError` during reader creation or page traversal. This contradicts the apparent graceful `0`/`[]` contract and can terminate processing.
- **User-visible impact:** Uploading an empty/corrupt document can crash the job rather than producing a controlled “invalid PDF” result. Treating all failures as zero pages would also make an invalid file indistinguishable from a valid empty document.
- **Reliable RED test:** Patch `_create_reader` to raise `pypdf.errors.EmptyFileError("empty")`; assert `count_pages` and `get_chapter_boundaries` return their documented fallback and `chunk_pdf` follows an explicit error contract. Current calls raise.
- **Smallest safe fix:** Catch `pypdf.errors.PdfReadError` (including `EmptyFileError`) at the PDF boundary, log a distinct invalid-document error, and make `chunk_pdf` consistently return no chunks or raise one domain exception. Do not silently relabel corruption as a valid zero-page PDF.
- **Verification:** `uv run pytest tests/unit/test_pdf_utils.py -q`

### F4 — Medium — PPTX conversion accepts a stale output as a fresh success (verified defect)

- **Evidence:** `src/flashcards_generator/infrastructure/pdf_utils.py:52-69` runs LibreOffice with a timeout and checks its return code, but `:71-78` accepts the expected path based only on `exists()`. The “PDF not created” test starts with an empty output directory (`tests/unit/test_pptx_converter.py:96-110`), so stale output is untested.
- **Observed mechanism:** If `output_dir/<stem>.pdf` predates the call and `soffice` returns 0 without replacing it, `convert` returns and logs the old file as converted.
- **User-visible impact:** Users can receive flashcards generated from a prior presentation with the same stem.
- **Reliable RED test:** Pre-create `output/test.pdf` containing a sentinel, mock the conversion subprocess to return 0 without touching it, and assert conversion does not report success. Current code returns the sentinel file.
- **Smallest safe fix:** Convert into a per-call temporary output directory, verify the expected regular PDF was created there, then atomically move it to the destination. This also avoids relying on timestamp granularity and prevents unrelated stale files from satisfying success.
- **Verification:** `uv run pytest tests/unit/test_pptx_converter.py -q`

### F5 — Medium — PDF readers and partial chunk files are not lifecycle-safe (verified leak; cleanup impact is a hypothesis)

- **Evidence:** Count/outline readers explicitly close streams at `src/flashcards_generator/infrastructure/pdf_utils.py:136-139` and `:196-199`, but chapter and fixed chunk readers created at `:300` and `:418` have no `finally`. Semantic extraction creates another reader at `semantic_chunker.py:84` with no close. Chunk files are written before each yield (`pdf_utils.py:400-410`, `:450-460`); cleanup is a separate opt-in method at `:462-470` and recognizes files only by `"_chunk_"` in the name.
- **Observed mechanism:** Normal completion, an exception while writing, or early generator close leaves source handles open until garbage collection. If a caller stops consuming or later processing fails, already-written chunks persist unless out-of-scope code always invokes `cleanup_chunks`.
- **User-visible impact:** Repeated/parallel jobs can exhaust file descriptors or prevent file replacement on stricter platforms. Orphan chunks consume disk and can be mistaken for current output. The orphan impact is **hypothetical** because caller cleanup could not be inspected.
- **Reliable RED test:** Return a fake reader with `stream.close = Mock()`, consume/close each generator (including after first yield), and assert close was called; current code fails. In a temporary output directory, raise from the consumer after the first yield and assert the API's chosen ownership policy removes partial chunks; current method itself does not.
- **Smallest safe fix:** Wrap each reader in `try/finally` and close its stream. Make temporary-output ownership explicit: preferably write in a caller-owned temporary directory, or track created paths and remove them on generator error/close while preserving successfully transferred outputs.
- **Verification:** `uv run pytest tests/unit/test_pdf_utils.py tests/test_semantic_chunking.py -q`

### F6 — High — Semantic chunks can exceed `max_tokens` (verified defect)

- **Evidence:** At `src/flashcards_generator/infrastructure/semantic_chunker.py:167-185`, overflow starts a new chunk as `overlap + sentence` without splitting the sentence or checking the resulting count. The final append at `:204-209` applies only a minimum, not the maximum.
- **Observed mechanism:** Exercising one 20-token sentence with `max_tokens=10`, `min_tokens=1`, and no overlap yielded one 20-token chunk. Overlap can also make an otherwise valid sentence exceed the maximum.
- **User-visible impact:** Downstream model context limits can be exceeded, causing request rejection, truncation, or unexpectedly high latency/cost.
- **Reliable RED test:** Stub token count to whitespace words and extraction to one 20-word `TextSegment`; assert every yielded chunk has at most 10 tokens. Current output has 20.
- **Smallest safe fix:** Split over-limit sentences on token boundaries (preserving text order), and budget overlap as part of the maximum. Assert the invariant before appending every chunk.
- **Verification:** `uv run pytest tests/test_semantic_chunking.py -q`

### F7 — High — Minimum-token handling silently drops document text (verified defect)

- **Evidence:** On overflow, prior text is appended only if it reaches `min_tokens` (`src/flashcards_generator/infrastructure/semantic_chunker.py:170-181`); only the bounded overlap is then carried forward. The final remainder is discarded below the minimum at `:204-209`. The existing empty-document test checks only that an invalid empty file yields nothing (`tests/test_semantic_chunking.py:231-239`).
- **Observed mechanism:** A non-empty ten-token document with defaults yields no chunks. On overflow after a 199-token prefix, up to 149 tokens can disappear because at most 50 overlap tokens survive.
- **User-visible impact:** Short documents produce no flashcards, and text near an overflow boundary can vanish without warning.
- **Reliable RED test:** Stub extraction to one non-empty ten-token segment and assert concatenated non-overlap output preserves it; current output is `[]`. Add a 199-token prefix followed by an over-limit sentence and assert every sentinel token appears at least once.
- **Smallest safe fix:** Treat `min_tokens` as a merge preference, not a deletion rule: merge a short remainder with an adjacent chunk when within `max_tokens`, otherwise emit it. Never replace un-emitted text with overlap-only context.
- **Verification:** `uv run pytest tests/test_semantic_chunking.py -q`

### F8 — Medium — Semantic boundary page metadata starts the next chunk one page early (verified defect)

- **Evidence:** After flushing at a boundary, `src/flashcards_generator/infrastructure/semantic_chunker.py:191-201` resets `current_start_page` to the just-consumed `segment.start_page` rather than the next segment. The next loop then adds the following page's text under that stale start.
- **Observed mechanism:** A deterministic three-page mutation with a boundary after segment index 1 yielded chunks `(pages 1-2)` and `(pages 2-3)`, although the second chunk contained text only from page 3.
- **User-visible impact:** Citations and page labels direct users to a page that is not represented in the chunk.
- **Reliable RED test:** Stub three one-token page segments and boundaries `[1]`; assert the second chunk is `(3,3)`. Current result is `(2,3)`.
- **Smallest safe fix:** Reset start metadata lazily when the next sentence is actually appended, or use `segments[i + 1].start_page` when one exists.
- **Verification:** `uv run pytest tests/test_semantic_chunking.py -q`

### F9 — Medium — Degenerate TF-IDF input preserves exact duplicate cards (verified defect)

- **Evidence:** `src/flashcards_generator/infrastructure/semantic_chunker.py:317-331` catches vectorizer failure and returns no similar pairs. All-empty or English-stop-word-only fronts produce “empty vocabulary.” The existing edge test only asserts the result is a list (`tests/test_semantic_chunking.py:244-254`), not that duplicates are detected.
- **Observed mechanism:** Calling `filter_deck` with duplicate fronts `"could would should"` and multi-word backs logs the empty-vocabulary warning, removes neither as trivial, and reports both kept with zero similar removals.
- **User-visible impact:** The quality filter fails precisely on low-information duplicate fronts and overstates `kept` while understating `similar_removed`.
- **Reliable RED test:** Pass two `("could would should", <multi-word back>)` cards and assert pair `(0,1)` is detected or one card is removed. Current result keeps both.
- **Smallest safe fix:** Detect normalized exact-front duplicates before TF-IDF; on empty vocabulary, return those exact pairs rather than treating analysis failure as evidence of uniqueness.
- **Verification:** `uv run pytest tests/test_semantic_chunking.py -q`

### F10 — High — Similarity filtering materializes quadratic work and memory (verified resource risk)

- **Evidence:** `src/flashcards_generator/infrastructure/semantic_chunker.py:317-318` computes a dense all-pairs cosine matrix; `:321-326` then scans every upper-triangle pair. This is explicit O(n²) memory and time.
- **Observed mechanism:** 20,000 cards require 400 million float similarities (about 3.2 GB at float64) before Python pair scanning and result storage; sufficiently similar decks can also accumulate O(n²) tuples.
- **User-visible impact:** Large imports can be killed for memory, stall, or amplify request latency even though only pairs above 0.85 are needed.
- **Reliable RED/mutation scenario:** Generate 20,000 unique non-trivial fronts under a bounded-memory worker (for example `ulimit -v 1500000`) and call `find_similar_cards`; the dense matrix exceeds the limit. A fixed implementation should complete without a dense NxN allocation.
- **Smallest safe fix:** L2-normalize the sparse TF-IDF matrix and process sparse dot products in bounded blocks, retaining only upper-triangle entries at or above threshold; additionally cap/batch at the system boundary if deck size is externally controlled.
- **Verification:** `uv run pytest tests/test_semantic_chunking.py -q` plus a bounded-memory performance test for the block implementation.

### F11 — Medium — The configured logger name is ignored and redirected sinks receive forced ANSI (verified defects)

- **Evidence:** `src/flashcards_generator/infrastructure/logging_config.py:43` binds `name` into Loguru `record["extra"]`, but the format uses Loguru's module field `{name}` at `:28`, not `{extra[name]}`. The sink unconditionally sets `colorize=True` at `:32-37`. The sole logging test checks only the pypdf level (`tests/unit/test_logging_config.py:10-18`).
- **Observed mechanism:** `get_logger("pdf_utils")` still formats the full Python module name. A captured/non-TTY stderr receives escape sequences because colorization is forced.
- **User-visible impact:** Component labels do not match the API, and redirected logs are polluted with ANSI codes, complicating ingestion and assertions.
- **Reliable RED test:** Monkeypatch `sys.stderr` to a non-TTY text buffer, call `configure_logging`, log through `get_logger("sentinel")`, await `logger.complete()`, and assert output contains `sentinel` and no `\x1b[`. Both assertions fail under the current format/sink.
- **Smallest safe fix:** Format `{extra[name]}` and use `colorize=None` (auto-detect) or `sys.stderr.isatty()`.
- **Verification:** `uv run pytest tests/unit/test_logging_config.py -q`

### F12 — Low — Logging configuration removes sinks it does not own (hypothesis / integration risk)

- **Evidence:** `src/flashcards_generator/infrastructure/logging_config.py:22` calls global `logger.remove()` with no sink id before adding stderr at `:32-38`.
- **Observed mechanism:** Any sink installed by an embedding process, test harness, telemetry integration, or earlier application phase is removed. Whether such sinks exist cannot be determined within the allowed scope.
- **User-visible impact:** **Hypothetical:** file/audit/telemetry logs silently stop after configuration, and repeated configuration can unexpectedly replace a host application's logging policy.
- **Reliable RED test:** Add a memory sink and retain its id, invoke `configure_logging`, emit a sentinel, complete queued logging, and assert the host sink received it. Current implementation removes it.
- **Smallest safe fix:** Store and remove only the sink id created by this module; leave foreign sinks intact. If global takeover is intentional, rename/document the API and test that explicit contract.
- **Verification:** `uv run pytest tests/unit/test_logging_config.py -q`

## Required-topic closure and non-findings

- **Page boundaries:** F1, F2, and F8 cover fixed, chapter, and semantic boundaries.
- **Empty/corrupt documents:** F3 covers pypdf exceptions; semantic extraction deliberately catches all exceptions at `semantic_chunker.py:102-104`, but this also collapses corruption and “no text” to the same empty result. F7 covers valid short/non-empty content.
- **Temporary files and cleanup:** F5 covers source handles and partial chunks. PPTX conversion currently writes directly to the final output; F4's per-call temporary directory is the safe correction.
- **External commands:** F4 is the concrete success-validation defect. Positively, arguments are passed as a list (no shell), return codes are checked, output is captured, and 5/120-second timeouts are present at `pdf_utils.py:29-38` and `:52-69`.
- **Token limits:** F6 and F7 cover maximum and minimum behavior.
- **Degenerate TF-IDF:** Semantic boundary analysis degrades to fixed intervals on vectorizer failure at `semantic_chunker.py:137-142`; quality deduplication fails open as described in F9.
- **Log sinks:** F11 and F12 cover formatting, color, queue-aware testing, and sink ownership. `enqueue=True` at `logging_config.py:37` makes explicit flushing important in deterministic tests.
- **Performance:** F10 is the dominant asymptotic risk. Additionally, chapter chunking stores every `PdfWriter` before writing at `pdf_utils.py:309-313,395-410`, so peak memory scales with the complete PDF; stream each completed writer instead of retaining all of them when refactoring that path.
