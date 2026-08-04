# DOMAIN GUIDANCE

Subordinate to the repository and `src/flashcards_generator/AGENTS.md`
instructions. Keep this package as the inward-facing model and boundary layer.

## LIVE MAP

- `entities.py`: `ChunkStatus`, `ChunkState`, `ChunkResumeManifest`,
  `Flashcard`, `Deck`.
- `value_objects.py`: configuration/source models `Config` and `SourceInfo`.
- `exceptions.py`: `FlashcardsGeneratorError` and contextual subclasses.
- `ports/flashcard_generator.py`: `FlashcardGeneratorPort`,
  `GenerationConfig`, `GenerationResult`.
- `ports/chunk_state.py`: `ChunkStatePort` resume/result persistence boundary.
- `ports/deck_repository.py`: `DeckRepositoryPort` deck persistence boundary.
- `ports/__init__.py`: public port re-exports; update `__all__` with exports.

## DEPENDENCIES

- Domain is inward-facing, not dependency-free: it uses Pydantic plus stdlib.
- Current stdlib needs include `abc`, `datetime`, `difflib`, `enum`,
  `pathlib`, and `typing`.
- Never import `application`, `infrastructure`, `adapters`, or `interfaces`.
- Express external generation and persistence needs through a domain port.
- Keep filesystem behavior, subprocesses, logging, and terminal output outside.
- Use `TYPE_CHECKING` for annotation-only `Path`, `Deck`, and `Flashcard`
  imports where runtime imports are unnecessary.

## PYDANTIC MODELS

- Models extend Pydantic v2 `BaseModel`; declare constraints/defaults with
  `Field` and configuration with `ConfigDict` when needed.
- Use `Field(default_factory=list)` for mutable collections.
- Use `Field(default_factory=datetime.now)` for per-instance timestamps.
- Preserve explicit optionality: `str | None`, `int | None`, and matching
  defaults are part of the persisted resume schema.
- `ChunkStatus` remains a string enum for stable serialized values.
- Entity behavior may stay close to its data: normalization, Anki formatting,
  deck card counts, mutation, and deduplication currently live in entities.
- Treat manifest field names and enum values as persistence compatibility
  surfaces; coordinate changes with the chunk-state implementation and tests.

## PORTS

- Define ports with `ABC` and `@abstractmethod`, not concrete service logic.
- Keep signatures fully typed and document return/absence semantics.
- Put implementation-specific behavior in outer-layer adapters/repositories.
- `FlashcardGeneratorPort` owns the NotebookLM-neutral generation lifecycle.
- `ChunkStatePort` owns manifest and per-chunk result persistence operations.
- `DeckRepositoryPort` owns deck save/load/existence operations.
- Annotation-only domain types belong behind `TYPE_CHECKING` when practical.
- Abstract stubs use `# pragma: no cover`; do not add operational fallbacks.

## EXCEPTIONS

- Derive domain failures from `FlashcardsGeneratorError`.
- Accept concrete context in subclass constructors, retain it on the instance,
  and pass a useful message to `super().__init__`.
- Keep messages specific to the failed source, notebook, artifact, or folder.
- Translate lower-level failures at outer boundaries and preserve their cause
  with `raise ... from error`; domain exceptions should not hide root causes.

## CHANGE RULES

- Do not add direct I/O, network clients, process execution, or service setup.
- Do not make entities depend on DTOs or concrete persistence formats.
- Do not introduce `Any`, blanket ignores, untyped public methods, or `print()`.
- Update affected port fakes, adapters, repositories, and schema tests when a
  domain contract changes; do not silently broaden a port for one adapter.
