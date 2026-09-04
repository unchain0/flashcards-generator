# Post-remediation security/concurrency code review

## Verdict

**PASS** (high confidence) for the five requested behavioral properties.  
**Code quality status:** WATCH  
**Recommendation:** APPROVE

The task-local `omo-agent-toolkit ulw-loop status --json` lookup returned `ULW_LOOP_PLAN_MISSING`; therefore this report uses the required fallback artifact path rather than an attempt directory. The shared root ULW plan was inspected separately at `.omo/ulw-loop/01a05f5f-031b-748b-a611-c92954a4249a/goals.json`.

## Scope and method

- Inspected the current unstaged diff (`git diff`), staged diff (empty), source seams, tests, and prior evidence. Prior evidence was treated as untrusted; notably, the old corrupt-provider probe showed the pre-remediation failure (exit 0 and a `.lock` artifact), so it was not used as success evidence.
- Inspected the actual current implementation for timeout/process-group lifecycle, PDF/JSON limit seams, sparse semantic boundaries, CSV publication, resume locking, and CLI exit propagation.
- Ran safe focused tests only (no NotebookLM executable):
  `uv run pytest -q tests/unit/test_pptx_converter.py tests/unit/test_pdf_utils.py tests/test_semantic_chunking.py tests/unit/test_csv_merger.py tests/unit/test_notebooklm_adapter.py tests/unit/test_notebooklm_client.py tests/unit/test_use_cases_edge_cases.py tests/unit/test_cli.py`
  -> **184 passed**.
- Ran `uv run ruff check` on reviewed source/tests and `uv run mypy src/flashcards_generator` -> **pass**. `git diff --check` -> **pass**. LSP diagnostics reported zero errors in source and tests.

## Requested-property verification

1. **PPTX/LibreOffice timeout process group: PASS.** `PPTXConverter._run_conversion` creates a new session and `_stop_process` signals that process group with SIGTERM, escalates to SIGKILL after the bounded cleanup timeout, and reaps it (`src/flashcards_generator/infrastructure/pdf_utils.py:126-166`). The focused test validates `start_new_session=True` and `os.killpg(..., SIGTERM)` (`tests/unit/test_pptx_converter.py:174-209`).
2. **PDF and JSON resource limits: PASS.** PDF byte and page limits run before `PdfReader` / page processing (`pdf_utils.py:221-257`, `semantic_chunker.py:122-161`); page-text length is checked for each extracted page (`semantic_chunker.py:172-186`). Both NotebookLM parser implementations cap raw JSON bytes before decoding and cap card-array cardinality before card allocation (`adapters/notebooklm_adapter.py:352-456`, `infrastructure/notebooklm_client.py:65-276`).
3. **No dense all-pairs semantic-boundary matrix: PASS.** Boundary calculation multiplies only adjacent sparse TF-IDF rows (`semantic_chunker.py:196-244`); it does not form the former all-pairs similarity matrix. The focused test blocks `csr_matrix.toarray` and passed (`tests/test_semantic_chunking.py:89-119`).
4. **Atomic CSV merge: PASS.** Merge writes and fsyncs a sibling temporary file, then atomically replaces the destination only after all source rows validate (`application/csv_merger.py:68-103`). The malformed-row regression preserves the previous destination (`tests/unit/test_csv_merger.py:241-257`).
5. **Corrupt/provider errors, resume lock, and CLI exit: PASS by source flow plus focused tests.** Resume locking is deferred until `_should_chunk_pdf` has successfully inspected the snapshot; corrupt or limit-failing non-chunked sources therefore never create a lock (`application/use_cases.py:264-328`). The lock context always releases `flock` and closes file descriptors (`infrastructure/chunk_state_repository.py:64-91`). Processing errors set `last_run_had_errors` (`application/use_cases.py:197-227`, `1418-1468`) and CLI maps that state to exit 1 (`interfaces/cli.py:318-340`). Focused tests cover corrupt source/no lock and error-state, plus CLI error-to-1 (`tests/unit/test_use_cases_edge_cases.py:40-63`, `tests/unit/test_cli.py:232-261`).

## Findings

### CRITICAL

None.

### HIGH

None.

### MEDIUM

1. **The reviewed production modules substantially exceed the skill-mandated 250 pure-LOC ceiling, which makes future security/concurrency changes difficult to review.** Examples: `src/flashcards_generator/application/use_cases.py` (its resume, filesystem-boundary, orchestration, provider and export responsibilities coexist), `src/flashcards_generator/infrastructure/pdf_utils.py`, `src/flashcards_generator/infrastructure/semantic_chunker.py`, and `src/flashcards_generator/adapters/notebooklm_adapter.py`. This is a maintainability concern, not a demonstrated failure of the requested fixes, so it is not an approval blocker.

2. **The actual provider-failure + lock-release outcome is only indirectly covered.** The current test proves corrupt input avoids lock-file creation (`tests/unit/test_use_cases_edge_cases.py:40-63`) and source inspection proves the `flock` release (`chunk_state_repository.py:64-91`), but no focused test starts a chunked job, induces a provider failure, then verifies another concurrent acquisition succeeds. This is a relevant concurrency coverage gap; it does not contradict the current implementation.

### LOW

1. **Lock-file artifacts intentionally remain after release.** `FileSystemChunkStateRepository.resume_lock` unlocks and closes the lock fd but does not unlink `.<stem>.lock` (`chunk_state_repository.py:64-91`). It is not a held resume lock and cannot block a later `flock` acquisition, but filesystem inspection alone can misidentify it as stale. Document this behavior or remove the empty artifact if operator expectations require absence.

2. **JSON file size is checked before `read_text`, but a hostile concurrent replacement can grow the file between `stat()` and the full read** (`notebooklm_adapter.py:435-446`; `notebooklm_client.py:257-268`). The post-read `_parse_json` byte check still rejects it before JSON decoding, but the read itself may allocate beyond the configured cap. The output location is application-controlled in the normal flow; treat this as a residual TOCTOU/resource-risk rather than a current functional failure.

## Skill-perspective check

**Ran.** Loaded and applied `remove-ai-slops` and `programming` from the installed OMO skills.

- `remove-ai-slops`: no deletion-only tests, prompt-prose tests, tautological requested-removal tests, or unnecessary parsing/normalization were found in the reviewed remediation seams. Its module-size criterion is violated (MEDIUM finding 1).
- `programming`: no new untyped escape hatch or brittle prose/prompt test was found in the reviewed seams. The same source-file size violation applies, and the provider-lock concurrency test gap conflicts with its preference for behavior-meaningful coverage. Neither issue demonstrates a correctness/regression failure for this goal.

## Residual risks

- Process groups cannot be forcibly reclaimed if the OS leaves a task in an uninterruptible state; the SIGTERM/SIGKILL/reap sequence is otherwise correct.
- The 512 MiB PDF and 16 MiB JSON ceilings are policy choices that still permit sizable, bounded workloads.
- The persistent but unlocked lock-file artifact and JSON stat/read TOCTOU are described above.

## Blockers

None.
