# AGENTS.md — application/

Use case orchestration and business logic coordination.

## Purpose

Implements application use cases by orchestrating domain entities through injected ports. Contains DTOs for data transfer and converters for format transformations.

## Structure

```
application/
├── use_cases.py      # GenerateFlashcardsUseCase, MergeFlashcardsUseCase
├── converter.py      # ClozeConverter (Cloze deletion formatting)
├── exporter.py       # DeckExporter (CSV/JSON output)
├── csv_merger.py     # CSV file merging operations
├── math_processor.py # MathML/LaTeX processing
└── dto/              # Pydantic DTOs
    ├── generate_request.py
    └── merge_request.py
```

## Critical Files

- `use_cases.py` — Main orchestration with DI (625 lines)
- `dto/generate_request.py` — Input validation DTO
- `converter.py` — Cloze pattern matching with regex

## Conventions

### Dependency Injection
```python
class GenerateFlashcardsUseCase:
    def __init__(
        self,
        generator: FlashcardGeneratorPort,
        converter: ClozeConverter | None = None,
    ) -> None:
        self.generator = generator
        self.converter = converter or ClozeConverter()  # Factory fallback
```
- Inject ports via `__init__`
- Use `None` + factory fallback for optional deps

### Constants (Magic Numbers)
```python
MAX_FILENAME_LEN = 50
SOURCE_WAIT_TIMEOUT = 600  # seconds
PDF_CHUNKING_THRESHOLD = 100
```
- SCREAMING_SNAKE_CASE at module level
- Include units in comments

### DTO Pattern
```python
class GenerateFlashcardsRequest(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    input_dir: Path
    difficulty: str = Field(default="medium")
```
- Use `model_config` (not inner `Config` class)
- Explicit runtime marker: `_ = Path`

### Exception Chaining
```python
except Exception as e:
    raise GenerationError(notebook_id, str(e)) from e
```
- Always chain with `from e`
- Wrap domain exceptions with context

## Anti-Patterns (FORBIDDEN)

- Never instantiate infrastructure directly (always inject)
- Never import from `interfaces/`
- Never use `print()` (use injected logger)
- Never catch bare `Exception` without chaining

## Parent Reference

See `src/flashcards_generator/AGENTS.md` for layer architecture overview.
