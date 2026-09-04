# Domain and request-contract audit

**Scope reviewed:** the requested domain entities, exceptions, ports, value objects, DTOs, and four directly related unit-test modules only. Baseline: `uv run pytest tests/unit/test_entities.py tests/unit/test_exceptions.py tests/unit/test_value_objects.py tests/unit/test_use_cases.py -q` passed (57 tests).

| Category | Result |
|---|---|
| Logic | Findings 2 and 3 |
| Typing | Finding 1 |
| Validation | Findings 2 and 4 |
| Error contracts | **No finding.** The reviewed domain exceptions retain their identifier/reason fields and derive from the common base; port methods are abstract. |
| Dead code | Finding 5 |
| Duplication | Finding 5 |
| Documentation | Finding 2 |

## Findings

### 1. `GenerationResult` cannot be constructed from a clean import

- **Severity:** high
- **Location:** `src/flashcards_generator/domain/ports/flashcard_generator.py:10-13,24-29`
- **Observed mechanism:** `Deck` is imported only under `TYPE_CHECKING`, while the Pydantic field `GenerationResult.deck: Deck` is a deferred forward reference. In a fresh process that imports only `GenerationResult`, Pydantic cannot resolve `Deck` and raises `PydanticUserError` on construction.
- **User-visible impact:** Any adapter returning this advertised result model can fail at runtime before it returns a generation result. Existing tests mask this by importing entities/use cases first.
- **RED/GREEN plan:** RED now: `uv run python -c "from flashcards_generator.domain.ports.flashcard_generator import GenerationResult; GenerationResult(deck={'name': 'audit'})"` exits with `PydanticUserError` saying the model is not fully defined. GREEN after the fix: the same command exits zero and constructs the deck.
- **Smallest safe fix:** Import `Deck` at runtime in this module (outside `TYPE_CHECKING`); it has no reverse dependency on the port. Alternatively, explicitly rebuild the model with a namespace containing `Deck`, but the direct import is simpler and reliable.
- **Targeted verification:** `uv run python -c "from flashcards_generator.domain.ports.flashcard_generator import GenerationResult; assert GenerationResult(deck={'name': 'audit'}).deck.name == 'audit'"`

### 2. `Deck.deduplicate` accepts thresholds outside its documented domain

- **Severity:** medium
- **Location:** `src/flashcards_generator/domain/entities.py:65-84`
- **Observed mechanism:** The docstring states a 0-1 similarity threshold, but no range/finite-value validation occurs. A threshold `<= 0` makes every later card a duplicate; `> 1` or `NaN` prevents every removal.
- **User-visible impact:** A malformed request/configuration can silently erase an entire deck or leave obvious duplicates, rather than producing an actionable validation error.
- **RED/GREEN plan:** Add a unit case expecting `ValueError` for `deck.deduplicate(-0.01)`, `deck.deduplicate(1.01)`, and `deck.deduplicate(float('nan'))`. It is RED now because each call returns normally; it is GREEN once invalid values are rejected before mutation.
- **Smallest safe fix:** At the start of `deduplicate`, reject non-finite thresholds and values outside inclusive `[0, 1]` with `ValueError`.
- **Targeted verification:** `uv run pytest tests/unit/test_entities.py -q`

### 3. Anki TSV serialization permits unescaped record and field delimiters

- **Severity:** medium
- **Location:** `src/flashcards_generator/domain/entities.py:56-59`
- **Observed mechanism:** `to_anki_format` interpolates untrusted `front`, `back`, and tag text directly into tab-separated output. A tab creates extra fields and a CR/LF creates extra records.
- **User-visible impact:** Flashcards containing normal pasted multiline material can import into Anki with shifted answers/tags or as unintended cards.
- **RED/GREEN plan:** Construct `Flashcard(front='question\tcontinued', back='answer\ncontinued')` and assert that export rejects unsafe delimiters (or has exactly two separators and one record after the selected escaping policy). It is RED now: the returned string contains the raw tab/newline and has a malformed TSV shape; it is GREEN once the contract is enforced.
- **Smallest safe fix:** Reject tab, CR, and LF in fields/tags with a clear `ValueError` at TSV export (or encode them using one documented Anki-compatible representation before joining); do not emit ambiguous TSV.
- **Targeted verification:** `uv run pytest tests/unit/test_entities.py -q`

### 4. Merge output "filename" admits traversal and non-filename paths

- **Severity:** medium
- **Location:** `src/flashcards_generator/application/dto/merge_request.py:15-18`
- **Observed mechanism:** `output_filename` is an unrestricted string. Values such as `../outside.csv`, `/tmp/outside.csv`, and `nested/out.csv` satisfy the request model although its contract calls it a filename.
- **User-visible impact:** A merge request can target a result outside the requested folder or fail unexpectedly when consumers expect one local filename.
- **RED/GREEN plan:** Add cases expecting `ValidationError` for `MergeCsvRequest(folder_path=tmp_path, output_filename='../outside.csv')` and `output_filename='nested/out.csv'`. They are RED now because both models are accepted; they are GREEN after filename validation.
- **Smallest safe fix:** Add a Pydantic field validator requiring a non-empty basename (`PurePath(value).name == value`) and rejecting absolute paths; retain the existing default.
- **Targeted verification:** `uv run pytest tests/unit/test_use_cases.py -q`

### 5. Redundant `_ = Path` assignments are duplicated in both DTO modules

- **Severity:** low
- **Location:** `src/flashcards_generator/application/dto/generate_request.py:7`; `src/flashcards_generator/application/dto/merge_request.py:7`
- **Observed mechanism:** `Path` is already required at runtime by the non-postponed field annotations. Assigning it to `_` neither changes Pydantic's namespace nor validation, despite the comment claiming it is required.
- **User-visible impact:** No current runtime effect; it is a maintenance hazard because it falsely signals a required Pydantic compatibility action in two request contracts.
- **RED/GREEN plan:** In a temporary mutation, remove the `Path` import while retaining `_ = Path`; importing either module fails with `NameError` (the import is necessary). In the candidate change, retain the import but remove only `_ = Path`; a fresh-process construction of each DTO remains GREEN. This falsifies the claimed need for the assignment.
- **Smallest safe fix:** Delete only the two `_ = Path` lines and their misleading comments; keep each `from pathlib import Path` import.
- **Targeted verification:** `uv run python -c "from pathlib import Path; from flashcards_generator.application.dto.generate_request import GenerateFlashcardsRequest; from flashcards_generator.application.dto.merge_request import MergeCsvRequest; GenerateFlashcardsRequest(input_dir=Path('.'), output_dir=Path('.')); MergeCsvRequest(folder_path=Path('.'))"`
