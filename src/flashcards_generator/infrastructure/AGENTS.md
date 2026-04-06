# AGENTS.md — infrastructure/

External service implementations and I/O operations.

## Purpose

Implements technical concerns: PDF parsing, API clients, file system operations, logging. Wraps external dependencies (pypdf, notebooklm, playwright).

## Structure

```
infrastructure/
├── pdf_utils.py        # PDFChunker, PPTXConverter
├── notebooklm_client.py # NotebookLM API wrapper
├── paths.py            # External tool discovery
└── logging_config.py   # Structured logging setup
```

## Critical Files

- `pdf_utils.py` — PDF chunking with `DEFAULT_THRESHOLD=100`
- `notebooklm_client.py` — API client with retry logic
- `paths.py` — Tool discovery (notebooklm executable)

## Conventions

### External Dependencies
```python
from pypdf import PdfReader  # External lib
from flashcards_generator.domain.exceptions import SourceProcessingError
```
- Import external libs freely
- Wrap external errors in domain exceptions

### Logging Pattern
```python
from flashcards_generator.infrastructure.logging_config import get_logger
logger = get_logger("pdf_utils")
logger.info(f"Processing {pdf_path.name}")
```
- Always use structured logging via `get_logger()`
- Pass module name to `get_logger()`

### Subprocess Handling
```python
try:
    result = subprocess.run(cmd, capture_output=True, timeout=120)
except subprocess.TimeoutExpired:
    logger.error("Operation timed out")
    return None
except FileNotFoundError:
    logger.warning("Tool not found")
    return None
```
- Always set timeouts
- Handle `FileNotFoundError` for missing tools
- Log errors, don't raise unless critical

### Path Utilities
```python
def find_notebooklm() -> str:
    """Find tool in PATH or UV installation."""
    notebooklm_path = shutil.which("notebooklm")
    if notebooklm_path:
        return notebooklm_path
    # Fallback to known UV paths...
```
- Use `shutil.which()` for PATH lookup
- Provide sensible fallbacks

## Anti-Patterns (FORBIDDEN)

- Never use `print()` (use logger)
- Never hardcode absolute paths (use `paths.py`)
- Never swallow exceptions silently (log them)
- Never import from `interfaces/`

## Dependencies

- `pypdf` — PDF parsing
- `notebooklm-py` — NotebookLM API
- `playwright` — Browser automation
- `loguru` — Structured logging

## Parent Reference

See `src/flashcards_generator/AGENTS.md` for layer architecture overview.
