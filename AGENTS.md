# PROJECT KNOWLEDGE BASE

**Generated:** 2026-08-04 14:17 -03
**Commit:** 4a74008
**Branch:** main

## OVERVIEW

Python 3.10 CLI that turns PDF/PPTX documents into Anki-compatible CSV
flashcards through the NotebookLM CLI. The code uses a `src/` layout,
Pydantic models, Loguru, pypdf, semantic chunking, and argparse.

## STRUCTURE

```text
.
├── main.py                         # Source-tree CLI wrapper
├── src/flashcards_generator/
│   ├── domain/                     # Models, exceptions, external ports
│   ├── application/                # Generation/merge orchestration
│   ├── infrastructure/             # PDF, state, logging, client utilities
│   ├── adapters/                   # NotebookLM port implementation
│   └── interfaces/                 # CLI and dependency composition
├── tests/                          # Unit, boundary/integration, fixtures
└── pyproject.toml                  # Package and all tool configuration
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add or change a CLI option | `src/flashcards_generator/interfaces/cli.py` | Parser, validation, wiring, exit codes |
| Change generation flow | `src/flashcards_generator/application/use_cases.py` | Single-file, chunk, resume, retry, cleanup |
| Change request validation | `src/flashcards_generator/application/dto/` | Pydantic request models; package re-exports |
| Change flashcard/deck schema | `src/flashcards_generator/domain/entities.py` | High-ripple Pydantic models |
| Change NotebookLM operations | `src/flashcards_generator/adapters/notebooklm_adapter.py` | Subprocess-facing port implementation |
| Change PDF/PPTX handling | `src/flashcards_generator/infrastructure/pdf_utils.py` | Splitting, conversion, 50-page threshold |
| Change semantic filtering | `src/flashcards_generator/infrastructure/semantic_chunker.py` | Token, TF-IDF, quality-filter behavior |
| Change resumable state | `src/flashcards_generator/domain/ports/chunk_state.py`, `src/flashcards_generator/infrastructure/chunk_state_repository.py` | Manifest persistence boundary |
| Change CSV/cloze output | `src/flashcards_generator/application/` | `exporter.py`, `converter.py`, `math_processor.py` |
| Trace behavior under tests | `tests/unit/test_use_cases*.py`, `tests/integration/test_resume_flow.py` | Main orchestration coverage |

## CODE MAP

Reference counts are LSP locations where available, otherwise direct module
import sites from the current source and tests.

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| `CLI` / `main` | Class/function | `src/flashcards_generator/interfaces/cli.py:36`, `:373` | 6 | Composition root and command dispatch |
| `GenerateFlashcardsUseCase` | Class | `src/flashcards_generator/application/use_cases.py:86` | 100+ | Central orchestration and resume flow |
| `FlashcardGeneratorPort` | ABC | `src/flashcards_generator/domain/ports/flashcard_generator.py:34` | 6 | NotebookLM/test-double contract |
| `NotebookLMAdapter` | Class | `src/flashcards_generator/adapters/notebooklm_adapter.py:46` | 5 | Concrete NotebookLM CLI boundary |
| `Flashcard` / `Deck` | Models | `src/flashcards_generator/domain/entities.py:45`, `:63` | 12 module sites | Core output schema |
| Domain exceptions | Hierarchy | `src/flashcards_generator/domain/exceptions.py` | 11 module sites | Cross-layer error contract |
| `PDFChunker` | Class | `src/flashcards_generator/infrastructure/pdf_utils.py` | 4 module sites | Large-document split seam |
| `FileSystemChunkStateRepository` | Class | `src/flashcards_generator/infrastructure/chunk_state_repository.py:18` | 4 module sites | Resume manifest/result storage |
| `get_logger` | Function | `src/flashcards_generator/infrastructure/logging_config.py` | 9 module sites | Shared Loguru binding |

## CONVENTIONS

- Python is exactly 3.10 (`.python-version`, `uv.lock`, pre-commit hook).
- Use `uv run ...`; dependencies live in the project environment, including
  the user-selected `notebooklm-py[browser]==0.8.1` and
  `playwright==1.61.0`.
- Ruff: target `py310`, 79 columns, double quotes, preview rules enabled.
- Mypy checks production code with typed definitions, strict equality, and
  unreachable/redundant-cast warnings; tests relax definition annotations.
- Models and request DTOs use Pydantic v2 `BaseModel`/`Field` conventions.
- External behavior is injected through ABC ports in `domain/ports/`.
- Log through `infrastructure.logging_config.get_logger`; CLI owns terminal
  presentation and process exit codes.
- Use `infrastructure.paths.find_notebooklm()` for executable discovery.
- Module constants are `SCREAMING_SNAKE_CASE`; include units in timeout/delay
  names or comments.

## ANTI-PATTERNS (THIS PROJECT)

- Do not add new inward dependency violations. Target flow is outer layers to
  application/domain; keep infrastructure and interfaces out of domain code.
- Do not construct new external services inside application use cases; add a
  domain port and wire its implementation in `interfaces/cli.py`.
- Do not use `print()` outside the CLI interface or hardcode absolute paths.
- Do not swallow external failures silently; log them or translate them to a
  context-rich exception and preserve causes with `raise ... from error`.
- Do not add blanket `noqa`, untyped definitions, `Any` escapes, or subprocess
  calls without explicit timeouts and cleanup.
- Do not describe current `tests/integration/` as live API coverage: both tests
  isolate NotebookLM with mocks/fakes.

## UNIQUE STYLES

- PDFs over 50 pages use chunking, retry/backoff, persisted resume manifests,
  per-chunk results, deduplication, and quality filtering.
- `GenerateFlashcardsUseCase` currently default-constructs `PDFChunker` and
  `QualityFilter` and imports infrastructure logging. Treat this as existing
  architecture debt, not a pattern to extend.
- NotebookLM responsibility is split: `NotebookLMAdapter` owns the command
  workflow; `NotebookLMClient` parses lower-level downloaded artifacts.
- Three entry surfaces intentionally converge on `interfaces.cli:main`:
  `main.py`, `python -m flashcards_generator`, and the `flashcards` script.

## COMMANDS

```bash
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/flashcards_generator
uv run pytest
uv run pytest tests/integration -m "not requires_api" --timeout=300
uv run task quality-gate
uv build
uv run pre-commit run --all-files --show-diff-on-failure
```

## NOTES

- Install Chromium once with `uv run playwright install chromium`; authenticate
  NotebookLM with `uv run notebooklm login` before real generation.
- CI runs lint/format/typecheck, unit tests, isolated integration tests, build,
  and a separate all-files pre-commit workflow.
- The working tree may contain unrelated user changes. Preserve them and avoid
  cleanup outside files explicitly in scope.
