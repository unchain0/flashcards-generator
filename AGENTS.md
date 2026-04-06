# AGENTS.md — flashcards_generator (Root)

## Goal

CLI tool that generates flashcards from PDFs using Google's NotebookLM API. Processes documents, converts them to structured flashcard format, exports to Anki/Markdown.

## Instructions

- Follow Clean Architecture: domain → application → infrastructure → interfaces
- All imports flow inward (interfaces can import infrastructure, never reverse)
- Use dependency injection via `ports/` protocols for testability
- Ruff linting enforced (line-length 88, Python 3.14 target)
- Pre-commit hooks must pass before commits

## Architecture

```
src/flashcards_generator/
├── domain/          # Entities, value objects, domain exceptions, ports (protocols)
├── application/     # Use cases, DTOs, business logic orchestration
├── infrastructure/  # External services: PDF parsing, NotebookLM API, logging
├── interfaces/      # CLI entry point (argparse-based)
└── adapters/        # External API adapters (NotebookLM client wrapper)
```

## Entry Points

- `main.py` → `flashcards_generator.interfaces.cli:main()`
- Module execution: `python -m flashcards_generator` (via `src/flashcards_generator/__main__.py`)
- Console script: `flashcards` (after `pip install -e .`)

## Key Patterns

- **Domain**: Pure Python, no external deps. Define entities in `entities.py`, ports in `ports/`
- **Application**: Use cases orchestrate via injected ports. DTOs in `dto/` folder
- **Infrastructure**: External service implementations. PDF chunking in `pdf_utils.py` (DEFAULT_THRESHOLD=100)
- **Interfaces**: Single CLI file using argparse. All I/O happens here

## Conventions

- Ruff: line-length 88, target Python 3.14
- Imports: Use `TYPE_CHECKING` for circular deps, absolute imports preferred
- Exceptions: Domain exceptions in `domain/exceptions.py`, infrastructure wraps external errors
- Logging: Structured logging via `infrastructure/logging_config.py`
- Paths: Use `infrastructure/paths.py` for all filesystem paths

## Testing

- `tests/unit/` — Unit tests with mocked ports
- `tests/integration/` — Integration tests with real services (rate-limited)
- `tests/fixtures/` — Sample PDFs and expected outputs
- Fixtures: `pytest` with `conftest.py` for shared mocks

## Critical Files

- `src/flashcards_generator/infrastructure/pdf_utils.py` — PDF chunking logic (threshold: 100)
- `src/flashcards_generator/application/use_cases.py` — Main orchestration flow
- `src/flashcards_generator/domain/ports/` — Abstract interfaces for all external deps
- `pyproject.toml` — Tool configs: Ruff, pytest, mypy

## Forbidden

- Never import interfaces from domain/application layers
- Never use `print()` outside CLI layer (use logging)
- Never hardcode paths (use `paths.py`)
- Never suppress type errors with `Any` without justification

## Dependencies

- Core: Pydantic, pypdf, loguru, rich, playwright, notebooklm-py
- Dev: Ruff, pytest, pre-commit, mypy
- External: NotebookLM API (via `notebooklm-client`)
