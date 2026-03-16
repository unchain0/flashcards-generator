# AGENTS.md — flashcards_generator/src

## Goal

Core source package implementing Clean Architecture layers for PDF-to-flashcard processing.

## Layer Responsibilities

| Layer | Purpose | Dependencies |
|-------|---------|--------------|
| `domain/` | Business entities, value objects, ports | None (pure Python) |
| `application/` | Use cases, orchestration, DTOs | domain/ |
| `infrastructure/` | External service implementations | domain/, application/ |
| `interfaces/` | CLI entry point | all layers (outer) |
| `adapters/` | External API wrappers | domain/ |

## Key Conventions

- **Domain purity**: No external imports in `domain/`. Use protocols (ports) for abstraction
- **DTO pattern**: All data transfer via Pydantic models in `application/dto/`
- **Dependency injection**: Services receive ports via `__init__`, never instantiate directly
- **Exception hierarchy**: Domain exceptions in `domain/exceptions.py`, infrastructure wraps with context

## Critical Files

- `domain/entities.py` — `PDFDocument`, `FlashcardDeck`, `Flashcard`
- `domain/ports/` — Repository and generator protocols
- `application/use_cases.py` — Main `GenerateFlashcardsUseCase`
- `infrastructure/pdf_utils.py` — PDFChunker with `DEFAULT_THRESHOLD=100`
- `infrastructure/notebooklm_client.py` — NotebookLM API client
- `interfaces/cli.py` — Typer CLI app

## Code Patterns

```python
# TYPE_CHECKING for circular imports
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from flashcards_generator.domain.ports import PDFRepositoryPort

# Dependency injection via constructor
class GenerateFlashcardsUseCase:
    def __init__(self, pdf_repo: PDFRepositoryPort) -> None:
        self._pdf_repo = pdf_repo

# Domain exceptions
from flashcards_generator.domain.exceptions import DomainError
```

## Testing

- Unit tests mock all ports
- Integration tests use real services (rate-limited)
- Fixtures in `tests/fixtures/`

## Parent Reference

See root `AGENTS.md` for project-wide conventions and architecture overview.
