# DOMAIN MODELS GUIDANCE

Subordinate to root and package instructions. This is the innermost data and
exception layer.

## LIVE MAP

- `entities.py`: `Flashcard`, `Deck`, `ChunkStatus`, `ChunkState`, and
  `ChunkResumeManifest`.
- `value_objects.py`: generation `Config` and source metadata.
- `exceptions.py`: `FlashcardsGeneratorError` and contextual subclasses.

## RULES

- Use Pydantic v2 plus standard-library types only.
- Never import `services`, `integrations`, or `delivery`.
- Ports belong to `services/ports`, not this package.
- Keep I/O, subprocesses, logging, HTTP, and persistence implementations out.
- Preserve serialized field names and enum values unless the repository and
  migration behavior are updated together.
- Use `Field(default_factory=...)` for mutable or per-instance defaults.
- Domain exceptions retain useful context; outer layers translate lower-level
  failures and preserve causes.
- Do not add `Any`, blanket ignores, or operational fallbacks.
