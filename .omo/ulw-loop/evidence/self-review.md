# Self-review — direct AnkiConnect export

Reviewed after the complete test, lint, type-check, format, build, and real
CLI runs.

## Criterion audit

- C001 passes: the real CLI generated a CSV and the local server recorded
  `createDeck` followed by `addNotes` for the requested hierarchical deck,
  with Cloze fields and duplicate-prevention options.
- C002 passes: a `result: null` response returns exit code 1, names
  AnkiConnect and `createDeck`, preserves the CSV, and does not claim success.
- C003 passes: the default command writes the existing two-column CSV and
  makes zero AnkiConnect requests. The temporary mutation produced RED and
  was restored.

## Structural review

- `AnkiExporterPort` is inward-facing and contains no I/O.
- `AnkiConnectAdapter` owns HTTP/JSON protocol handling and translates
  transport, HTTP, shape, and per-note failures into `AnkiConnectError`.
- CLI wiring is opt-in; the default path does not instantiate or call the
  Anki exporter.
- Existing CSV export remains in the use case and was not replaced.
- Tests use a local HTTP server, a deterministic NotebookLM executable, and
  temporary directories; their server threads and temporary roots are
  cleaned up.
- Existing unrelated worktree changes were preserved.

## Residual risks and limitations

- The installed Anki version and custom Cloze field schema were not available;
  the adapter intentionally targets the standard `Text` and `Extra` fields.
- `httpx2` was unavailable in the Python 3.10 environment, so the project
  consistent, explicitly declared `httpx` dependency is used instead.
- The shared LSP daemon was unreachable and no basedpyright/pyright binary was
  installed; `mypy` and targeted ruff checks were clean.
- No commit was created because the user did not request one.

Verdict: no success-criterion blocker found; tier HEAVY remains justified by
the external integration and was satisfied by the recorded evidence.
