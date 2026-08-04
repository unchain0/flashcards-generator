# PACKAGE KNOWLEDGE BASE

## OVERVIEW

Installable `flashcards_generator` package. Implements document discovery,
NotebookLM generation, chunk resume, card cleanup, and Anki-oriented export.
Root `AGENTS.md` owns project-wide tooling and style; this file narrows package
boundaries and symbol locations.

## STRUCTURE

```text
flashcards_generator/
├── domain/          # Pydantic entities, exceptions, value objects, ABC ports
├── application/     # Requests, orchestration, conversion, export, CSV merge
├── adapters/        # FlashcardGeneratorPort -> NotebookLM CLI adapter
├── infrastructure/  # PDF/PPTX, resume storage, logging, paths, client helpers
├── interfaces/      # argparse surface and dependency composition
├── __main__.py      # `python -m flashcards_generator`
└── __init__.py      # package metadata only
```

## WHERE TO LOOK

| Change | Primary location | Package-specific note |
|--------|------------------|-----------------------|
| Card/deck schema | `domain/entities.py` | `Flashcard`, `Deck` |
| Chunk resume schema | `domain/entities.py` | `ChunkStatus`, `ChunkState`, `ChunkResumeManifest` |
| Generator contract | `domain/ports/flashcard_generator.py` | `GenerationConfig`, `GenerationResult`, `FlashcardGeneratorPort` |
| Resume contract | `domain/ports/chunk_state.py` | `ChunkStatePort` |
| Deck persistence contract | `domain/ports/deck_repository.py` | Declared port; not wired by the CLI |
| Generate request validation | `application/dto/generate_request.py` | Include/exclude/explicit-file inputs |
| Merge request validation | `application/dto/merge_request.py` | CSV merge inputs |
| Generation lifecycle | `application/use_cases.py` | Discovery, chunking, retry, resume, cleanup |
| Card/output transforms | `application/converter.py`, `exporter.py`, `math_processor.py` | Cloze and Anki formatting |
| CSV-only merge | `application/csv_merger.py` | Independent of NotebookLM generation |
| NotebookLM workflow | `adapters/notebooklm_adapter.py` | Concrete generator port implementation |
| Resume persistence | `infrastructure/chunk_state_repository.py` | Atomic manifest/result JSON writes |
| Document helpers | `infrastructure/pdf_utils.py`, `semantic_chunker.py` | Page chunks, PPTX conversion, quality filter |
| CLI behavior and wiring | `interfaces/cli.py` | Parser, auth/language setup, exit codes |

## BOUNDARIES / CONVENTIONS

- Domain may use Pydantic and standard-library types; it must not import outer
  package layers.
- Domain ports are ABCs: `FlashcardGeneratorPort`, `ChunkStatePort`, and
  `DeckRepositoryPort`. Put external contracts there, implementations outside.
- Application DTOs cross the CLI/use-case boundary; domain entities represent
  generated cards, decks, and persisted chunk state.
- `interfaces/cli.py` is the composition root. It constructs
  `NotebookLMAdapter`, `FileSystemChunkStateRepository`, requests, and use cases.
- `main.py`, package `__main__.py`, and the installed `flashcards` script all
  converge on `flashcards_generator.interfaces.cli:main`.
- `NotebookLMAdapter` owns the active command workflow. `NotebookLMClient` is a
  lower-level infrastructure helper and is not wired into the CLI path.
- Resume storage serializes domain models; keep manifest and per-chunk result
  changes compatible across `ChunkStatePort`, repository, and use case.
- Known debt: `application/use_cases.py` imports infrastructure logging,
  `PDFChunker`, and `QualityFilter`; it also provides concrete fallbacks. Do not
  treat these dependency inversions as the pattern for new services.
- Nested `application/`, `domain/`, and `infrastructure/` guides add local
  context only; source and this package guide win when their examples are stale.

## ANTI-PATTERNS

- Do not resurrect stale `PDFDocument`, `FlashcardDeck`, `ClozeBlock`,
  `FlashcardSide`, or `MergeFlashcardsUseCase` names; those symbols do not exist.
- Do not construct a new external integration inside application code. Define a
  domain port and wire the adapter in `interfaces/cli.py`.
- Do not move argparse, terminal presentation, authentication, or process exit
  decisions below `interfaces/`.
- Do not collapse `adapters/` and `infrastructure/`: the active NotebookLM port
  adapter and technical helpers have distinct responsibilities.
- Do not claim `tests/integration/` exercises live NotebookLM; current coverage
  uses isolated fakes/mocks.

## PARENT

Follow repository-root `AGENTS.md` first for global architecture, commands,
quality gates, and worktree rules.
