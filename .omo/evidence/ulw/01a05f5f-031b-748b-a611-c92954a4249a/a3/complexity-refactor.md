# Complexity refactor attempt

Date: 2026-09-02

## Baseline RED

- `uv run task quality-gate`: exit 1. The strict B(6) gate reported 38
  offenders. Highest results were
  `GenerateFlashcardsUseCase._process_large_pdf` D(25), the interrupted-resume
  integration scenario C(19), `QualityFilter.find_similar_cards` C(18), and
  `PDFChunker._chunk_by_chapters` C(18).
- `COVERAGE_FILE=<mktemp> uv run pytest`: exit 0, 426 passed in 62.03s. The
  disposable coverage file was removed immediately after the run.
- The existing `pyproject.toml` and `uv.lock` dependency/version and mode
  changes were read before edits and were not edited.

## Cohesive extractions completed

1. Extracted duplicate comparison, cloze-content validation, and NotebookLM
   identifier-value parsing into private helpers. Focused entity, converter,
   client, and adapter suite: exit 0, 107 passed.
2. Extracted CSV source discovery/row iteration/deduplication/writing and CLI
   cleanup selection. Focused CSV/CLI suites: exit 0 (11 and 21 tests in the
   recorded runs).
3. Extracted NotebookLM notebook response loading, date filtering, and progress
   versus non-progress deletion loops. Focused adapter suites: exit 0, 34
   passed.
4. Extracted semantic chunk accumulation/overflow and sparse similarity pair
   collection. Combined focused regression run after all completed extractions:
   exit 0, 193 passed. The final semantic-focused run also exited 0 with 31
   passed.

These extractions reduced strict-gate offenders from 38 to 26 without changing
public names, signatures, schemas, command options, persistence, subprocess,
path, locking, or error contracts.

## Final verification and terminal result

- `uv run ruff check <seven changed production files>`: exit 0, all checks
  passed.
- `uv run mypy src/flashcards_generator`: exit 0, no issues in 30 source files.
- LSP error diagnostics on all seven changed production files: zero errors.
- `git diff --check`: exit 0.
- Final `uv run task quality-gate`: **exit 1** with 26 remaining offenders.
  The highest residuals are the unchanged central use-case D(25), interrupted
  resume test C(19), PDF chapter chunking C(18), source snapshot C(16), PDF
  discovery/chapter boundaries C(14), and associated B(7)-C(12) production and
  test helpers. No threshold, command, ignore, or generated-code bypass was
  used.
- A second full 426-test run was not claimed after extraction because the
  strict gate remained terminally red; focused coverage remained green.

## Changed-path scope receipt

This attempt edited only these production paths plus this evidence report:

- `src/flashcards_generator/domain/entities.py`
- `src/flashcards_generator/application/converter.py`
- `src/flashcards_generator/application/csv_merger.py`
- `src/flashcards_generator/infrastructure/notebooklm_client.py`
- `src/flashcards_generator/adapters/notebooklm_adapter.py`
- `src/flashcards_generator/interfaces/cli.py`
- `src/flashcards_generator/infrastructure/semantic_chunker.py`
- `.omo/evidence/ulw/01a05f5f-031b-748b-a611-c92954a4249a/a3/complexity-refactor.md`

No test, manifest, lockfile, threshold, script, documentation, workflow, or
dependency file was edited by this attempt. No commit, destructive git command,
live authentication, external write, fixed sleep, skip, xfail, or suppression
was used.

## Cleanup and residual risks

- Removed the disposable baseline coverage file. No build or authentication
  artifacts were generated. Existing repository caches and unrelated working
  tree changes were left untouched.
- Terminal residual: the strict quality gate is still red. The partial private
  extractions are focused-test, Ruff, mypy, and LSP clean, but completion still
  requires decomposing the 26 listed production/test hotspots and rerunning the
  full 426+ suite and all final commands.
- Several edited modules were already above the 250 pure-LOC review ceiling;
  this bounded attempt did not perform an unrelated module architecture split.
