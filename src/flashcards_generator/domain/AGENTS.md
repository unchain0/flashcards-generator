# AGENTS.md — domain/

Pure business logic layer. No external dependencies.

## Purpose

Domain entities, value objects, exception hierarchy, and port protocols (abstract interfaces). This layer defines WHAT the system does, not HOW.

## Structure

```
domain/
├── entities.py       # Flashcard, Deck (Pydantic models)
├── exceptions.py     # Exception hierarchy with context
├── value_objects.py  # ClozeBlock, FlashcardSide
└── ports/            # Abstract interfaces (protocols)
    ├── flashcard_generator.py  # FlashcardGeneratorPort
    └── deck_repository.py      # DeckRepositoryPort
```

## Critical Files

- `entities.py` — `Flashcard`, `Deck` with Anki formatting
- `exceptions.py` — `FlashcardsGeneratorError` base class, context-rich subclasses
- `ports/flashcard_generator.py` — Main port with 8 abstract methods

## Conventions

### Domain Purity
- **NO external imports** except `pydantic`, `datetime`, `typing`
- Use `TYPE_CHECKING` for `Path` imports
- All entities are Pydantic `BaseModel` subclasses

### Exception Pattern
```python
class GenerationError(FlashcardsGeneratorError):
    def __init__(self, notebook_id: str, reason: str) -> None:
        self.notebook_id = notebook_id  # Store context
        self.reason = reason
        super().__init__(f"Generation failed for {notebook_id}: {reason}")
```
- Always chain exceptions: `raise X from e`

### Port Protocol Pattern
```python
from abc import ABC, abstractmethod

class FlashcardGeneratorPort(ABC):
    @abstractmethod
    def create_notebook(self, title: str) -> str:
        pass  # pragma: no cover
```
- Use `ABC` + `@abstractmethod`
- Add `# pragma: no cover` to abstract stubs
- Include docstrings with return types

## Anti-Patterns (FORBIDDEN)

- Never import from `application/`, `infrastructure/`, `interfaces/`
- Never use `print()` (domain has no I/O)
- Never hardcode paths
- Never suppress type errors with `Any`

## Parent Reference

See `src/flashcards_generator/AGENTS.md` for layer architecture overview.
