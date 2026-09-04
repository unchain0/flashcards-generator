# L1 contract/value-validation evidence

## Scope and changes

Implemented only B1-B3 from the verified domain/DTO audit:

- `GenerationResult` imports `Deck` at runtime, allowing clean-process construction.
- `Deck.deduplicate` rejects non-finite and out-of-range thresholds before inspecting or mutating cards.
- `MergeCsvRequest.output_filename` accepts only a nonempty relative basename.
- Removed the two proven-redundant `_ = Path` assignments while retaining their runtime `Path` imports.

Changed lane files:

- `src/flashcards_generator/domain/ports/flashcard_generator.py`
- `src/flashcards_generator/domain/entities.py`
- `src/flashcards_generator/application/dto/generate_request.py`
- `src/flashcards_generator/application/dto/merge_request.py`
- `tests/unit/test_entities.py`
- `tests/unit/test_contracts.py` (new focused contract test)
- `.omo/evidence/ulw/01a05f5f-031b-748b-a611-c92954a4249a/a2/L1-contracts.md`

`pyproject.toml` and `uv.lock` were already modified before this lane. At final status, unrelated in-progress changes also existed under `infrastructure/{logging_config,pdf_utils,semantic_chunker}.py` and their L4 tests; none were written or altered by this lane. The lane's production/test writes are limited to the paths listed above.

## RED evidence (tests added before production edits)

### B1: `GenerationResult` clean import

```console
$ uv run pytest tests/unit/test_contracts.py::test_generation_result_constructs_from_clean_import -q; printf 'EXIT:%s\n' $?
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 1 item

tests/unit/test_contracts.py F                                           [100%]

=================================== FAILURES ===================================
_____________ test_generation_result_constructs_from_clean_import ______________
tests/unit/test_contracts.py:29: in test_generation_result_constructs_from_clean_import
    assert result.returncode == 0, result.stderr
E   AssertionError: Traceback (most recent call last):
E       File "<string>", line 1, in <module>
E       File "/home/avell/Projects/unchain0/flashcards-generator/.venv/lib/python3.10/site-packages/pydantic/main.py", line 263, in __init__
E         validated_self = self.__pydantic_validator__.validate_python(data, self_instance=self)
E       File "/home/avell/Projects/unchain0/flashcards-generator/.venv/lib/python3.10/site-packages/pydantic/_internal/_mock_val_ser.py", line 100, in __getattr__
E         raise PydanticUserError(self._error_message, code=self._code)
E     pydantic.errors.PydanticUserError: `GenerationResult` is not fully defined; you should define `Deck`, then call `GenerationResult.model_rebuild()`.
E
E     For further information visit https://errors.pydantic.dev/2.13/u/class-not-fully-defined
E
E   assert 1 == 0
=========================== short test summary info ============================
FAILED tests/unit/test_contracts.py::test_generation_result_constructs_from_clean_import
============================== 1 failed in 0.30s ===============================
EXIT:1
```

### B2: invalid deduplication thresholds

```console
$ uv run pytest tests/unit/test_entities.py::TestDeck::test_deduplicate_rejects_invalid_threshold_without_mutation -q; printf 'EXIT:%s\n' $?
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 3 items

tests/unit/test_entities.py FFF                                          [100%]

=================================== FAILURES ===================================
_ TestDeck.test_deduplicate_rejects_invalid_threshold_without_mutation[-0.01] __
tests/unit/test_entities.py:42: in test_deduplicate_rejects_invalid_threshold_without_mutation
    with pytest.raises(ValueError):
E   Failed: DID NOT RAISE <class 'ValueError'>
__ TestDeck.test_deduplicate_rejects_invalid_threshold_without_mutation[1.01] __
tests/unit/test_entities.py:42: in test_deduplicate_rejects_invalid_threshold_without_mutation
    with pytest.raises(ValueError):
E   Failed: DID NOT RAISE <class 'ValueError'>
__ TestDeck.test_deduplicate_rejects_invalid_threshold_without_mutation[nan] ___
tests/unit/test_entities.py:42: in test_deduplicate_rejects_invalid_threshold_without_mutation
    with pytest.raises(ValueError):
E   Failed: DID NOT RAISE <class 'ValueError'>
=========================== short test summary info ============================
FAILED tests/unit/test_entities.py::TestDeck::test_deduplicate_rejects_invalid_threshold_without_mutation[-0.01]
FAILED tests/unit/test_entities.py::TestDeck::test_deduplicate_rejects_invalid_threshold_without_mutation[1.01]
FAILED tests/unit/test_entities.py::TestDeck::test_deduplicate_rejects_invalid_threshold_without_mutation[nan]
============================== 3 failed in 0.07s ===============================
EXIT:1
```

### B3: merge output filename containment

```console
$ uv run pytest tests/unit/test_contracts.py::test_merge_output_filename_must_be_relative_basename -q; printf 'EXIT:%s\n' $?
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 4 items

tests/unit/test_contracts.py FFFF                                        [100%]

=================================== FAILURES ===================================
____________ test_merge_output_filename_must_be_relative_basename[] ____________
tests/unit/test_contracts.py:34: in test_merge_output_filename_must_be_relative_basename
    with pytest.raises(ValidationError):
E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
_____ test_merge_output_filename_must_be_relative_basename[../outside.csv] _____
tests/unit/test_contracts.py:34: in test_merge_output_filename_must_be_relative_basename
    with pytest.raises(ValidationError):
E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
____ test_merge_output_filename_must_be_relative_basename[/tmp/outside.csv] ____
tests/unit/test_contracts.py:34: in test_merge_output_filename_must_be_relative_basename
    with pytest.raises(ValidationError):
E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
_____ test_merge_output_filename_must_be_relative_basename[nested/out.csv] _____
tests/unit/test_contracts.py:34: in test_merge_output_filename_must_be_relative_basename
    with pytest.raises(ValidationError):
E   Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
=========================== short test summary info ============================
FAILED tests/unit/test_contracts.py::test_merge_output_filename_must_be_relative_basename[]
FAILED tests/unit/test_contracts.py::test_merge_output_filename_must_be_relative_basename[../outside.csv]
FAILED tests/unit/test_contracts.py::test_merge_output_filename_must_be_relative_basename[/tmp/outside.csv]
FAILED tests/unit/test_contracts.py::test_merge_output_filename_must_be_relative_basename[nested/out.csv]
============================== 4 failed in 0.07s ===============================
EXIT:1
```

## GREEN evidence

```console
$ uv run pytest tests/unit/test_contracts.py::test_generation_result_constructs_from_clean_import -q
1 passed in 0.27s
EXIT:0

$ uv run pytest tests/unit/test_entities.py::TestDeck::test_deduplicate_rejects_invalid_threshold_without_mutation -q
3 passed in 0.04s
EXIT:0

$ uv run pytest tests/unit/test_contracts.py::test_merge_output_filename_must_be_relative_basename -q
4 passed in 0.04s
EXIT:0

$ uv run pytest tests/unit/test_entities.py tests/unit/test_value_objects.py tests/unit/test_use_cases.py -q
53 passed in 61.01s (0:01:01)
EXIT:0

$ uv run python -c "from flashcards_generator.domain.ports.flashcard_generator import GenerationResult; assert GenerationResult(deck={'name': 'audit'}).deck.name == 'audit'"
EXIT:0

$ uv run python -c "from pathlib import Path; from flashcards_generator.application.dto.generate_request import GenerateFlashcardsRequest; from flashcards_generator.application.dto.merge_request import MergeCsvRequest; GenerateFlashcardsRequest(input_dir=Path('.'), output_dir=Path('.')); MergeCsvRequest(folder_path=Path('.'))"
EXIT:0
```

LSP diagnostics reported no diagnostics for each changed production file.

## Bounded residual risk

No residual behavior is unverified within B1-B3. This lane validates `output_filename` lexically at the request boundary; filesystem containment, symlink handling, and merge execution remain intentionally outside this L1 scope.
