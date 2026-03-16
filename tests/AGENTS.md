# AGENTS.md — flashcards_generator/tests

## Goal

Test suite for the flashcards generator CLI tool.

## Structure

```
tests/
├── unit/           # Unit tests with mocked dependencies
├── integration/    # Integration tests with real services (rate-limited)
└── fixtures/       # Sample PDFs and expected outputs
```

## Testing Strategy

- **Unit tests**: Mock all external dependencies (NotebookLM API, filesystem)
- **Integration tests**: Test with real services, minimal runs to avoid rate limits
- **Fixtures**: Reusable test data in `fixtures/`

## Conventions

- Use `pytest` with `conftest.py` for shared fixtures
- Mock ports/interfaces, never mock domain entities
- Test file naming: `test_<module>.py`
- Coverage target: >80%

## Running Tests

```bash
# All tests
pytest

# Unit only
pytest tests/unit/

# With coverage
pytest --cov=flashcards_generator --cov-report=term-missing

# Integration (careful - uses real API)
pytest tests/integration/ -v
```

## Key Fixtures

- `conftest.py` — Shared mocks for ports, sample PDFs
- `fixtures/sample.pdf` — Small test PDF
- `fixtures/expected_output.json` — Expected flashcard format

## Parent Reference

See root `AGENTS.md` for project conventions and architecture.
