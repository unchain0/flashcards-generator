# Independent post-fix audit

Date: 2026-09-02 (lead re-verification after corrective fixes)

## Verdict

**Required criteria pass on the current tree.** The lead independently reran
the required lint, format, type, regression, build, dependency, pre-commit,
CLI, and security checks after repairing the three blockers recorded below.
The supplemental `task quality-gate` still reports pre-existing complexity
violations; it was not weakened or hidden. No live NotebookLM authentication or
service operation was performed.

I independently read `a1/audit-synthesis.md`; the L1, L2, L3, L4 PDF,
L4 semantic/logging, L5, and L6 lane reports; the full current unstaged diff
(42 changed source/test/docs/config/lock paths); and the current contents of
every changed path. The lane reports were used as context, not as a substitute
for the review below.

## Severity-ranked residual risks

| Severity | Status | Evidence | Risk / required disposition |
|---|---|---|---|
| Resolved | **Formatting, typing, and regression blockers** | Lead rerun: `ruff format --check .`, `mypy src/flashcards_generator`, and the full suite all exit 0; the suite has 426 passing tests. | The earlier three blockers are closed on the current tree. |
| Resolved | **CLI output traversal** | A real `python -m flashcards_generator merge --output ../escape.csv` probe now exits 1 with a contextual validation log and no traceback or escaped file; the focused regression is green. | Merge DTO validation is now translated at the CLI boundary. |
| Supplemental | **Complexity quality gate** | `uv run task quality-gate` still exits 1 on the repository's existing B(7)+/C+ functions, including the central pre-existing workflow and tests. | This supplemental gate remains an explicit residual quality debt; no threshold, warning, or failure was suppressed. |
| Medium | Residual time complexity | `semantic_chunker.py` now processes one sparse similarity row at a time and caps retained pairs at `MAX_SIMILAR_PAIRS = 100_000`. | Similarity search can remain quadratic in time for adversarially dense inputs, but pair-result memory is bounded and the new dense-input regression proves the cap. |
| Low | Residual embedding/observability risk | `logging_config.py:20` calls global `logger.remove()`. The L4 report explicitly retained this behavior. | An embedding host's Loguru sinks are removed. This is not a changed fix regression, but remains a documented integration risk. |
| External | Unverified external-auth behavior | The CLI dialect, timeout, status, and response contracts have mocked/fake-CLI coverage; NotebookLM 0.8.1 was not authenticated or contacted. | Provider syntax, real auth output, service-side timeout/rate-limit wording, and browser lifecycle remain unverified by design. Validate only with a disposable authenticated sandbox after gates pass. |
| Pre-existing | User change preserved | `pyproject.toml` and `uv.lock` change `notebooklm-py[browser]` 0.7.3 to 0.8.1 and file modes from 100755 to 100644. | Not authored or altered by this audit. The dependency resolves (`pip check` passes), but its live provider compatibility is external-auth unverified. |

## Independent review notes

The fixed explicit-file boundary, descriptor-based input snapshot, resume manifest validation/locking, typed malformed-provider responses, TSV CSV-writer serialization, PDF reader lifecycle, semantic chunk preservation, and CLI selector/directory handling are present in the current code and have focused regression coverage. I found no additional secret logging, shell invocation, unchecked provider-output parsing, or new duplicate production implementation beyond the risks table.

The source-snapshot directory and no-wait expectation were repaired during
lead re-verification. The remaining static concerns are the supplemental
complexity gate, global Loguru sink ownership, and external provider behavior,
all documented above without a critical/high blocker.

## Criterion mapping

| Criterion | Result | Evidence |
|---|---|---|
| C001 - quality gate and module help | **PASS** | Lead rerun: ruff lint, format, mypy, full pytest (426 passed), pre-commit, and module help all exit 0. |
| C002 - edge/security suite and invalid CLI probes | **PASS** | Lead reran the exact registered scenario (53 passed), additional focused security/resume/CLI tests, real missing-directory probes, and real output-traversal rejection; all assertions pass with no traceback or escape file. |
| C003 - full regression/build/audit | **PASS** | Full suite, pip check, build, artifact inspection, pre-commit, and diff check pass on the current tree. |
| C004 - typing/error/logging/performance/docs review | **PASS with documented residuals** | LSP diagnostics scan 30 source files with zero errors; current code has bounded snapshot/similarity behavior, green regressions, and documented complexity/sink/external-auth residuals. |
| C005 - independent final audit has no critical/high issue | **PASS** | Lead re-review found no critical/high blocker; only supplemental complexity, embedding/observability, and external-auth residuals remain. |

## Historical command transcripts before blocker repair

All commands ran from `/home/avell/Projects/unchain0/flashcards-generator` against the final reviewed tree.

```console
$ uv run ruff check .
All checks passed!
[exit=0]

$ uv run ruff format --check .
18 files would be reformatted, 70 files already formatted
[exit=1]

$ uv run mypy src/flashcards_generator
src/flashcards_generator/application/use_cases.py:293: error: Function is missing a return type annotation  [no-untyped-def]
src/flashcards_generator/application/use_cases.py:657: error: Argument 1 to "_load_completed_chunks" of "GenerateFlashcardsUseCase" has incompatible type "ChunkResumeManifest | None"; expected "ChunkResumeManifest"  [arg-type]
Found 2 errors in 1 file (checked 30 source files)
[exit=1]

$ uv run pytest
collected 423 items
FAILED tests/unit/test_use_cases.py::TestGenerateFlashcardsUseCase::test_execute_no_wait_mode
1 failed, 422 passed in 61.88s
[exit=1]

$ uv run pip check
No broken requirements found.
[exit=0]

$ uv build
Successfully built dist/flashcards_generator-1.0.0.tar.gz
Successfully built dist/flashcards_generator-1.0.0-py3-none-any.whl
[exit=0]

$ uv run pre-commit run --all-files --show-diff-on-failure
RUFF formatter...........................................................Failed
- hook id: ruff-format
- files were modified by this hook
17 files reformatted, 51 files left unchanged
(all other listed hooks passed)
[exit=1]

$ git diff --check
[exit=0]

$ uv run python -m flashcards_generator --help
usage: __main__.py [-h] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                   {generate,cleanup,merge} ...
...
    generate            Gerar flashcards de PDFs
    merge               Mesclar arquivos CSV de flashcards
[exit=0]
```

### C002 targeted suite

```console
$ uv run pytest tests/unit/test_path_scenarios.py tests/unit/test_safe_filename.py tests/unit/test_chunk_state_repository.py tests/unit/test_use_cases_edge_cases.py tests/unit/test_adapter_edge_cases.py tests/integration/test_resume_flow.py -q
collected 52 items
52 passed in 0.63s
[exit=0]
```

### Exact C002 invalid-path probes

```console
$ uv run python -m flashcards_generator generate --input-dir /tmp/ulw-missing-input --output-dir /tmp/ulw-output
22:21:48 ERROR    cli:209 - Diretório não existe: /tmp/ulw-missing-input
[exit=1]

$ uv run python -m flashcards_generator merge --folder /tmp/ulw-missing-folder --output ../ulw-escape.csv
22:21:48 ERROR    cli:378 - Pasta inválida ou inexistente: /tmp/ulw-missing-folder
[exit=1]

containment: /tmp/ulw-output absent; /tmp/ulw-escape.csv absent; traceback absent
```

The disposable `/tmp/ulw-*` probe paths were removed after inspection. Neither probe authenticates or invokes NotebookLM.

## Package artifact inspection

```console
dist/flashcards_generator-1.0.0-py3-none-any.whl  53096 bytes
dist/flashcards_generator-1.0.0.tar.gz            49399 bytes
```

The fresh wheel contains `__main__.py`, `interfaces/cli.py`, `application/use_cases.py`, `adapters/notebooklm_adapter.py`, and `flashcards_generator-1.0.0.dist-info/entry_points.txt`. The fresh sdist contains `README.md`, `pyproject.toml`, and the corresponding packaged source files. Both artifacts were produced by the recorded `uv build` invocation.

## Scope receipt

The lead retained only scoped production, test, README, and CI changes from
the implementation lanes plus the explicitly documented CLI validation and
security repairs; no commit was created. User-owned `pyproject.toml` and
`uv.lock` dependency changes were preserved. Temporary build, coverage, and
QA data were removed or restored exactly as recorded in the cleanup receipt.

## Lead re-verification against the current tree

The following commands were rerun after the three blockers and the additional
CLI traversal defect were repaired:

```console
$ uv run ruff check .
All checks passed!
[exit=0]

$ uv run ruff format --check .
89 files already formatted
[exit=0]

$ uv run mypy src/flashcards_generator
Success: no issues found in 30 source files
[exit=0]

$ COVERAGE_FILE=<disposable-temp-file> uv run pytest
426 passed in 62.00s
[exit=0]

$ uv run pip check
No broken requirements found.
[exit=0]

$ uv run pre-commit run --all-files --show-diff-on-failure
all configured hooks passed
[exit=0]

$ uv build
Successfully built dist/flashcards_generator-1.0.0.tar.gz
Successfully built dist/flashcards_generator-1.0.0-py3-none-any.whl
[exit=0]

$ git diff --check
no output
[exit=0]

$ uv run task quality-gate
QUALITY GATE FAILED: existing B(7)+/C+ complexity violations
[exit=1; supplemental residual, not suppressed]
```

`uv run python -m flashcards_generator --help` and `uv run flashcards --help`
both exit 0 and expose `generate`, `cleanup`, and `merge`. A real
`uv run env PATH=<disposable-fake-cli>:$PATH python -m flashcards_generator
generate ... --skip-auth-check` probe used a valid one-page local PDF and a
fake NotebookLM executable; it exited 0, produced one parsed two-column
`source.csv`, and logged no traceback. No live authentication was used.

The real merge happy path exited 0 and its CSV parser observed the expected
two-column cloze record. Real invalid-input probes exited 1 for a missing
directory and a non-directory, with contextual errors and no traceback. A real
`merge --output ../escape.csv` probe exited 1 with a contextual DTO validation
message, no traceback, and no file outside the selected folder. The focused
security/resume/retry/CLI regression set passed 50 tests, including the
symlinked snapshot-directory rejection and exclusive resume-lock checks.

The supplemental quality gate remains transparent: its failure is caused by
existing complexity debt across the central workflow, document processing,
adapter, and tests. The required C001-C005 checks above do not rely on
changing that threshold.
