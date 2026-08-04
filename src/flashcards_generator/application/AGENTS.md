# AGENTS.md — `application/`

Application-layer guidance. Root and package `AGENTS.md` rules still apply.

## Scope

- Orchestrate domain entities and ports; keep CLI presentation in `interfaces/`.
- `use_cases.py` is 1,058 lines and owns the complete generation workflow.
- `GenerateFlashcardsUseCase.execute()` is the public generation entry point.
- `CsvMerger.merge()` is the separate CSV-merge application operation.

## File Map

- `use_cases.py`: scan inputs, validate scanned paths, mirror output paths,
  run normal or chunked generation, resume, retry, filter, clean up, export.
- `dto/generate_request.py`: Pydantic request for directories, generation
  options, timeout/wait behavior, resume, filters, and explicit files.
- `dto/merge_request.py`: Pydantic request for merge root, output name,
  deduplication, and recursive discovery.
- `converter.py`: convert a domain `Flashcard` to cloze form; return `None`
  for unusable/trivial output; preserve card metadata.
- `exporter.py`: write JSON, quoted two-column CSV, Anki TSV, or Markdown.
  Generation currently calls only `export_csv()`.
- `math_processor.py`: protect math during cloze construction and normalize
  dollar-delimited LaTeX to Anki `\(...\)` / `\[...\]` notation.
- `csv_merger.py`: merge sorted CSV inputs, skip malformed rows, optionally
  deduplicate `(front, back)`, and exclude its own output file.

## Generation Flow

- Recursive discovery accepts PDF and PPTX; scanned candidates reject
  symlinks, paths outside the input root, non-files, unsupported types,
  and empty files. Explicit-file handling is a distinct path.
- Existing destination CSV files are skipped.
- PDFs over the 50-page threshold enter chunk processing; PPTX does not.
- Normal flow creates one notebook, adds/waits for the source, generates,
  downloads/parses a temporary JSON artifact, converts cards, then saves CSV.
- Chunk flow creates a notebook per chunk, retries selected runtime/rate-limit
  failures with bounded exponential backoff, and combines successful decks.
- Resume mode validates a source signature and chunk count, persists manifest
  plus per-chunk decks through `ChunkStatePort`, and skips completed chunks.
- Combined chunk decks are deduplicated, then filtered for trivial and similar
  cards before export. Successful runs remove resume artifacts.
- Track every created notebook and temporary artifact; preserve `finally`
  cleanup when changing early returns or error paths.

## Dependencies

- Require `FlashcardGeneratorPort` in the constructor.
- Accept `ChunkStatePort | None` for resumable persistence.
- Optional collaborators follow the current `None`-then-default pattern:
  `ClozeConverter`, `DeckExporter`, and `PDFChunker`.
- Prefer constructor injection for new replaceable behavior and external I/O.
- Direct imports of infrastructure logging, `PDFChunker`, and `QualityFilter`,
  plus local `QualityFilter()` construction, are known debt—not precedent.

## Errors and Boundaries

- Use domain exceptions from `domain/exceptions.py`; preserve causal chains
  when translating failures.
- Per-document generation logs expected processing failures and returns
  `None`, allowing the remaining inputs to continue.
- Chunk internals log and re-raise operational failures so retry/state logic
  can record the outcome; do not broaden retries without classifying safety.
- Cleanup failures are intentionally best-effort in designated paths. Log or
  suppress only where the existing cleanup contract calls for it.
- `CsvMerger` translates all merge-boundary failures to `CSVMergeError` with
  `raise ... from error`; retain that consistent public contract.

## Change Discipline

- Keep request validation in DTOs and domain data in domain models.
- Preserve stable ordering, two-column CSV shape, UTF-8, and quoted CSV output.
- Test normal, chunked, resume, retry, timeout, partial-failure, and cleanup
  paths when touching orchestration; converter/export changes need edge cases
  for existing clozes, trivial answers, math, and malformed parsed cards.
