# L2 generation/state lane

## Evidence reviewed

Re-read before implementation:

- `a1/audit-synthesis.md` (B4-B10 in the L2 map)
- `a1/audit-application.md` (A1-A4, A8)
- `a1/audit-state-path.md` (F1-F8)

## RED evidence

Regression tests were added before production edits in the permitted L2 test files.

- Initial working-tree RED: `L2-red.txt` - 12 failures / 23 passes. It demonstrates explicit traversal and unsupported files accepted, post-discovery swap reaching `_process_pdf`, an empty no-wait CSV, missing/corrupt/foreign resume state failures, same-size/restored-mtime reuse, transient `OSError` escaping retry, symlink-temp victim overwrite, missing `fsync`/private modes, and missing resume ownership.
- Reproduced baseline RED against copies of the two pre-edit production files: `L2-red-verified.txt` - 13 failures / 22 passes. This includes the corrected normal-generation dedup assertion (`2 != 1`) and records exact pytest tracebacks for each regression. The temporary baseline directory was removed after capture.

No test uses a sleep, polling loop, or timing dependency. The only concurrent-ownership assertion is a non-blocking filesystem lease assertion.

## Implementation

- Explicit paths now use the discovery safety boundary and accepted paths are resolved under the input root.
- Each selected input is copied through an `O_NOFOLLOW` descriptor to a temporary snapshot before use, so a symlink swap after discovery is rejected instead of submitted.
- Result subdirectories are resolved and checked beneath the resolved output root.
- Resume signatures are streaming SHA-256 digests. Matching manifests must also match the logical source and deck layout. Completed entries must have a unique in-range index, their derived in-resume result filename, valid JSON, and matching card count; invalid/missing results regenerate only that chunk. Corrupt manifests are discarded and restarted.
- State writes use random `O_EXCL|O_NOFOLLOW` sibling temp names, `0600` files, `0700` state directories, data and directory `fsync`, no-follow reads, and a non-blocking per-resume lease. The lease is held across remote work, export, and cleanup, so a competing filesystem-backed run exits before remote work.
- No-wait decks remain visible to callers but do not create a CSV completion marker or clear resume state.
- Ordinary decks use the existing deck deduplication semantics. `OSError` is retried by the chunk retry lane and an exhausted failure records `FAILED` through the existing manifest path. `KeyboardInterrupt` remains uncaught.

Public request and port APIs were not changed. `_process_pdf` and `_process_large_pdf` gained optional private snapshot parameters only.

## GREEN verification

```text
uv run pytest tests/unit/test_use_cases_edge_cases.py tests/unit/test_use_cases_resume.py tests/unit/test_chunk_state_repository.py tests/integration/test_resume_flow.py -q
35 passed in 0.67s
```

Full output: `L2-green.txt`.

```text
uv run ruff check src/flashcards_generator/application/use_cases.py src/flashcards_generator/infrastructure/chunk_state_repository.py tests/unit/test_use_cases_edge_cases.py tests/unit/test_use_cases_resume.py tests/unit/test_chunk_state_repository.py tests/integration/test_resume_flow.py
All checks passed!
```

LSP diagnostics reported no errors for both changed production files. `uv build` also completed and produced both the source distribution and wheel.

## Filesystem containment and manifest integrity

`L2-filesystem-check.txt` records the before/after state probe:

```text
explicit_selection ['inside.pdf']
result_contained True
manifest_round_trip True
manifest_contained True
state_modes 0o700 0o600
```

The corresponding RED transcripts show the prior unsafe state: traversal/unsupported selection, state symlink victim modification, no durability calls, and externally trusted resume results. The GREEN probe proves the accepted source and serialized manifest stayed beneath the selected output root, round-tripped through the repository, and received private modes.

## Changed paths

This lane changed only the permitted production paths:

- `src/flashcards_generator/application/use_cases.py`
- `src/flashcards_generator/infrastructure/chunk_state_repository.py`

and permitted test paths:

- `tests/unit/test_use_cases_edge_cases.py`
- `tests/unit/test_use_cases_resume.py`
- `tests/unit/test_chunk_state_repository.py`

No integration-test source edit was required. The working tree contained unrelated pre-existing changes outside this lane; they were not edited.
