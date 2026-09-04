# L4 semantic chunking and logging

## Scope

Production changes are limited to:

- `src/flashcards_generator/infrastructure/semantic_chunker.py`
- `src/flashcards_generator/infrastructure/logging_config.py`

Regression tests are limited to:

- `tests/test_semantic_chunking.py`
- `tests/unit/test_logging_config.py`

No PDF utility was edited by this lane. `configure_logging()` retains its existing global sink ownership behavior; the unproven foreign-sink hypothesis was not changed or tested.

## Regression coverage

The added deterministic tests assert these invariants directly:

- A `None` page extraction does not prevent later page text from being retained.
- Every sentinel token in short and oversized text is emitted, and every output chunk is at or below `max_tokens`.
- Boundary-only chunks start on their first represented page; chunks containing overlap retain the overlapped page in metadata.
- Stop-word-only exact duplicates are reported deterministically when TF-IDF has no vocabulary.
- Similarity filtering does not invoke dense `cosine_similarity`.
- A redirected stderr renders the configured component and contains no ANSI control sequence.

## RED transcript

Command:

```console
uv run pytest \
  tests/test_semantic_chunking.py::TestSemanticChunkerRegressions \
  tests/test_semantic_chunking.py::TestQualityFilterRegressions \
  tests/unit/test_logging_config.py::test_configured_component_is_rendered_without_ansi_on_redirected_stderr \
  -q
```

Output before production edits:

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 7 items

tests/test_semantic_chunking.py FFFFFF                                   [ 85%]
tests/unit/test_logging_config.py F                                      [100%]

=================================== FAILURES ===================================
_ TestSemanticChunkerRegressions.test_extract_text_skips_none_page_without_losing_later_page _
tests/test_semantic_chunking.py:311: in test_extract_text_skips_none_page_without_losing_later_page
    assert chunker.extract_text_from_pdf(tmp_path / "sample.pdf") == [
E   AssertionError: assert [] == [TextSegment(...undary=False)]
E     
E     Right contains one more item: TextSegment(text='retained page', start_page=2, end_page=2, token_count=3, is_sentence_boundary=False)
----------------------------- Captured stderr call -----------------------------
2026-09-01 21:51:22.557 | ERROR | flashcards_generator.infrastructure.semantic_chunker:extract_text_from_pdf:103 - Failed to extract text from /tmp/pytest-of-avell/pytest-495/test_extract_text_skips_none_p0/sample.pdf: 'NoneType' object has no attribute 'strip'
_ TestSemanticChunkerRegressions.test_short_and_oversized_text_is_preserved_within_max_tokens _
tests/test_semantic_chunking.py:329: in test_short_and_oversized_text_is_preserved_within_max_tokens
    assert [token for text, _, _ in chunks for token in text.split()] == tokens
E   AssertionError: assert [] == ['sentinel_0'...tinel_5', ...]
E     
E     Right contains 22 more items, first extra item: 'sentinel_0'
_ TestSemanticChunkerRegressions.test_boundary_chunk_starts_at_first_represented_page _
tests/test_semantic_chunking.py:350: in test_boundary_chunk_starts_at_first_represented_page
    assert [(start, end) for _, start, end in chunks] == [(1, 2), (3, 3)]
E   assert [(1, 2), (2, 3)] == [(1, 2), (3, 3)]
E     
E     At index 1 diff: (2, 3) != (3, 3)
_ TestSemanticChunkerRegressions.test_overlap_chunk_metadata_includes_the_overlapped_page _
tests/test_semantic_chunking.py:368: in test_overlap_chunk_metadata_includes_the_overlapped_page
    assert chunks == [
E   AssertionError: assert [('Alpha. Bet...elta.', 2, 2)] == [('Alpha. Bet...elta.', 1, 2)]
E     
E     At index 1 diff: ('Gamma. Delta.', 2, 2) != ('Gamma. Delta.', 1, 2)
_ TestQualityFilterRegressions.test_stop_word_duplicate_is_detected_when_tfidf_has_no_vocabulary _
tests/test_semantic_chunking.py:382: in test_stop_word_duplicate_is_detected_when_tfidf_has_no_vocabulary
    assert QualityFilter().find_similar_cards(cards) == [(0, 1, 1.0)]
E   assert [] == [(0, 1, 1.0)]
E     
E     Right contains one more item: (0, 1, 1.0)
_ TestQualityFilterRegressions.test_similarity_filter_does_not_materialize_a_dense_matrix _
tests/test_semantic_chunking.py:400: in test_similarity_filter_does_not_materialize_a_dense_matrix
    assert QualityFilter().find_similar_cards(cards) == [(0, 1, 1.0)]
src/flashcards_generator/infrastructure/semantic_chunker.py:318: in find_similar_cards
    similarities = cosine_similarity(tfidf_matrix)
tests/test_semantic_chunking.py:388: in fail_if_called
    pytest.fail("dense cosine similarity matrix must not be created")
E   Failed: dense cosine similarity matrix must not be created
___ test_configured_component_is_rendered_without_ansi_on_redirected_stderr ____
tests/unit/test_logging_config.py:37: in test_configured_component_is_rendered_without_ansi_on_redirected_stderr
    assert "sentinel_component" in output
E   AssertionError: assert 'sentinel_component' in '\x1b[32m21:51:22\x1b[0m \x1b[1mINFO    \x1b[0m \x1b[36mtests.unit.test_logging_config:33\x1b[0m - captured message\n'
=========================== short test summary info ============================
FAILED tests/test_semantic_chunking.py::TestSemanticChunkerRegressions::test_extract_text_skips_none_page_without_losing_later_page
FAILED tests/test_semantic_chunking.py::TestSemanticChunkerRegressions::test_short_and_oversized_text_is_preserved_within_max_tokens
FAILED tests/test_semantic_chunking.py::TestSemanticChunkerRegressions::test_boundary_chunk_starts_at_first_represented_page
FAILED tests/test_semantic_chunking.py::TestSemanticChunkerRegressions::test_overlap_chunk_metadata_includes_the_overlapped_page
FAILED tests/test_semantic_chunking.py::TestQualityFilterRegressions::test_stop_word_duplicate_is_detected_when_tfidf_has_no_vocabulary
FAILED tests/test_semantic_chunking.py::TestQualityFilterRegressions::test_similarity_filter_does_not_materialize_a_dense_matrix
FAILED tests/unit/test_logging_config.py::test_configured_component_is_rendered_without_ansi_on_redirected_stderr
============================== 7 failed in 0.64s ===============================
```

Exit status: 1.

## Implementation

- Extraction skips `None`/empty page results individually.
- Chunk assembly retains page-aware sentence fragments, emits short remainders, splits oversized text, and budgets overlap inside `max_tokens`.
- Exact normalized duplicate fronts are retained on TF-IDF failure; sparse block products replace dense all-pairs cosine matrices.
- Log formatting uses the configured component binding and enables ANSI only for a TTY.

## GREEN transcript

Command:

```console
uv run pytest tests/test_semantic_chunking.py tests/unit/test_logging_config.py -q
```

Output:

```text
============================= test session starts ==============================
platform linux -- Python 3.10.21, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/avell/Projects/unchain0/flashcards-generator
configfile: pyproject.toml
plugins: timeout-2.4.0, asyncio-1.2.0, cov-7.1.0, anyio-4.14.2
asyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 32 items

tests/test_semantic_chunking.py ..............................           [ 93%]
tests/unit/test_logging_config.py ..                                     [100%]

============================== 32 passed in 0.77s ==============================
```

Exit status: 0.

LSP diagnostics for both production files reported no diagnostics.

## Workspace note

During this lane, other workers added unrelated modifications under PDF, domain, DTO, and dependency paths. This lane did not edit those files; its authored source/test changes remain within the declared scope.
