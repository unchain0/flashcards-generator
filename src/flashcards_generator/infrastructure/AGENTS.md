# AGENTS.md — infrastructure/

Concrete filesystem, document-processing, subprocess, and logging helpers.
Root and package `AGENTS.md` rules still apply; this file only narrows them.

## Live Map

- `pdf_utils.py` — `PPTXConverter`; `PDFChunker` page counting, outline-aware
  splitting, overlap, and temporary-chunk cleanup.
- `document_limits.py` — finite PDF and provider-artifact resource bounds.
- `semantic_chunker.py` — token estimation, PDF text segmentation, TF-IDF
  boundaries, overlap, trivial-card rejection, and deck deduplication.
- `notebooklm_client.py` — lower-level NotebookLM CLI/artifact helper and JSON
  parser. It is not the application port implementation.
- `chunk_state_repository.py` — Pydantic JSON manifests and per-chunk decks for
  resumable generation.
- `logging_config.py` — Loguru stderr sink and third-party noise controls.
- `paths.py` — NotebookLM executable discovery.

## Ownership Boundaries

- `adapters/notebooklm_adapter.py` owns the active `FlashcardGeneratorPort`
  command workflow: create/add/wait/generate/download/delete, retry policy, and
  domain-error translation.
- Keep orchestration and retry policy out of these infrastructure utilities.
  Do not grow `NotebookLMClient` into a second competing workflow.
- Infrastructure may depend inward on domain contracts. Never import from
  `interfaces/`, and do not perform terminal presentation here.

## Document Processing

- Preserve `PDFChunker` defaults unless behavior is intentionally changed:
  50-page threshold, 30-page chunks, 5-page overlap.
- Prefer usable PDF outline boundaries; otherwise use fixed-size overlapping
  chunks. Continue opening real-world PDFs with `strict=False`.
- Close pypdf reader streams on every path. Cleanup must target only generated
  chunk paths and tolerate individual `OSError` failures.
- Enforce the shared finite PDF size, page-count, and extracted-text limits
  before retaining untrusted document content.
- PPTX conversion is optional: probe `soffice` with a bounded call, convert
  headlessly into the supplied directory, and verify the expected PDF exists.
- Semantic enrichment must preserve page metadata and token bounds. `tiktoken`
  may degrade to word estimation; TF-IDF failures may degrade to fixed
  boundaries or unfiltered results only when the fallback is explicit/logged.

## Subprocesses, Errors, and Logs

- Every subprocess call needs an explicit timeout, captured output where useful,
  and cleanup/termination appropriate to the API used.
- Translate required-operation failures at the owning boundary into contextual
  domain exceptions and preserve causes with `raise ... from error`.
- Sentinel returns (`None`, `False`, `[]`, `0`) are reserved for documented
  optional or best-effort behavior; log enough path/operation context to debug.
- Existing broad catches in semantic analysis and the silent fallback in
  `NotebookLMClient.generate_flashcards()` are debt, not patterns to copy.
- Use `get_logger("module_name")`; do not `print()`. Avoid logging full generated
  content, credentials, or unbounded subprocess output.
- `configure_logging()` owns sink replacement, stderr formatting, enqueueing,
  level selection, and suppression of noisy `pypdf` logs.

## Paths and Resume State

- Pass `Path` objects through filesystem APIs; never hardcode machine-specific
  locations. Create only the required parent/output directories.
- `find_notebooklm()` returns `shutil.which("notebooklm")` when resolved and the
  command name `"notebooklm"` as the final fallback; callers handle launch errors.
- Keep manifest/result serialization on `ChunkResumeManifest` and `Deck`
  Pydantic methods so schema validation occurs on load.
- Preserve atomic state writes: write a temporary sibling, then `replace()` the
  destination; remove the temporary file on failure and re-raise.
- Keep deletion narrowly scoped to the requested manifest or chunk-result
  directory. Never broaden cleanup to a parent/workspace path.

## Runtime Dependencies

- Python is 3.10. `notebooklm-py[browser]==0.8.1` and
  `playwright==1.61.0` are project dependencies, alongside pypdf, scikit-learn,
  tiktoken, and Loguru; do not treat NotebookLM or Playwright as undeclared tools.
