# Final code review 2: Textual migration

## Verdict

- **result:** FAIL
- **codeQualityStatus:** BLOCK
- **recommendation:** REQUEST_CHANGES

The settings, domain cancellation protocol, README command names, detailed merge counts, and compact footer are now present. The migration still cannot be approved: concrete NotebookLM management cancellation is not connected to the TUI, generation cleanup remains uninterruptible through the cancellation token, and the required quality gates are not green.

## Skill-perspective check

No skill-loading interface was available in this child task. I applied the documented `remove-ai-slops` and `programming` criteria directly to the complete production/test diff. The diff still violates both perspectives: the production result adapter scans unrelated historical CSV data, cancellation is hidden behind an optional `getattr` instead of a typed service contract, lifecycle tests substitute a fake with behavior the production service does not implement, the PTY exit check can wait forever, and a backup test artifact remains in the tree.

## Findings

### CRITICAL

None.

### HIGH

1. **Production NotebookLM management cancellation is still disconnected.** `NotebookLMPanel.cancel_active()` looks up an optional `cancel_management` method dynamically (`src/flashcards_generator/interfaces/tui/screens/notebooklm.py:136-144`), but the production `ApplicationServices` class ends at `save()` and has no such method (`src/flashcards_generator/interfaces/composition.py:269-315`). An independent runtime probe of `create_services()` printed `cancel_management: False`. Consequently `FlashcardsApp.on_unmount()` (`src/flashcards_generator/interfaces/tui/app.py:138-146`) cancels only the Textual worker handle; it does not reach `ApplicationWorkflows.cancel_management()` or `NotebookLMManagement.cancel_active()`, so an unbounded login subprocess can survive app shutdown. The regression test is misleading because its fake directly implements `cancel_management` (`tests/tui/test_notebooklm_settings_picker.py:78-90`) and never exercises `ApplicationServices` or a child process.

2. **Generation cancellation can still block in notebook teardown.** Generation always enters `_cleanup_notebooks()` from the `finally` path (`src/flashcards_generator/application/use_cases.py:377-388,1025-1040`), while `NotebookLMAdapter.delete_notebook()` explicitly invokes `_run_command(..., cancellable=False)` (`src/flashcards_generator/adapters/notebooklm_adapter.py:562-569`). `GeneratePanel.cancel_generation()` only cancels the token (`src/flashcards_generator/interfaces/tui/screens/generate.py:341-345`), so cancellation during or before cleanup cannot terminate that delete command and may leave the worker/process running until the 900-second adapter timeout. `test_cancelled_chunk_generation_deletes_notebook_and_reaps_commands` uses an immediately completing delete mock and therefore does not prove blocked-delete cancellation (`tests/unit/test_generation_progress_cancellation.py:148-207`). The final behavior transcript's claim that cleanup reaped all child processes is not supported by that test.

3. **The objective's required quality gates fail on the final tree.** `uv run ruff format --check .` reports `src/flashcards_generator/interfaces/tui/screens/settings.py` and `tests/tui/test_results_merge.py` unformatted. `uv run task quality-gate` exits 1 for `SettingsPanel.on_button_pressed` B(7), `NotebookLMManagement._stop_process` B(7), and `test_settings_panel_round_trips_persisted_preferences` C(11). C006 explicitly requires format and project quality gates to exit zero, so the recorded completion evidence is stale or inaccurate.

### MEDIUM

1. **Generation Results can include stale and unrelated CSVs.** `UseCaseGenerationWorkflow.generate()` recursively returns every CSV currently under the output root (`src/flashcards_generator/interfaces/composition.py:111-115`) rather than outputs produced by this invocation. Results then chooses the first sorted path for copy/open (`src/flashcards_generator/interfaces/tui/screens/results.py:104-112`). This unnecessary broad extraction can present or act on a prior run's file.

2. **Desktop actions report success for failed commands.** `copy_text()` and `open_path()` use `check=False` and return `True` without checking the subprocess return code (`src/flashcards_generator/infrastructure/desktop_actions.py:10-28,31-44`). The TUI can claim "CSV copied" or "Open command sent" after the helper exits nonzero.

3. **The real-PTY test has an unbounded teardown wait and weak behavior coverage.** `_wait_for_exit()` calls blocking `waitpid(pid, 0)` with no deadline (`tests/integration/test_tui_pty.py:38-41`), and `_read_until(fd, b"", timeout=3)` returns after the first read because the empty marker always matches (`tests/integration/test_tui_pty.py:33,70`). A quit regression can therefore hang the suite rather than fail within a bounded timeout. The test verifies only initial Generate text and `q`, while the C005 happy/invalid/cancel claims remain prose receipts rather than raw xterm.js-rendered evidence.

### LOW

1. `tests/unit/test_generation_progress_cancellation.py.orig` remains as an untracked backup file. It is repository slop and should not ship.

## Verified improvements

- Settings now persist and map `resume`, `include_pattern`, and `exclude_pattern` into the real generation DTO (`infrastructure/settings.py`, `screens/settings.py`, `screens/generate.py`).
- The domain port now depends on the domain-owned `CancellationPort`, not an application concrete type (`domain/ports/cancellation.py`, `domain/ports/flashcard_generator.py`).
- README non-interactive examples consistently use `flashcards-cli`; primary commands use the Textual entrypoints.
- Detailed merge before/written/duplicate counts flow from `CsvMerger.merge_detailed()` through `MergeOutcome` to the UI.
- The 52-column receipt lists the complete footer, and the app bindings include q/g/r/m/n/s/Ctrl+R/Escape/help.

## Independent verification

- `uv run pytest -q`: **494 passed in 115.72s**.
- `uv run mypy src/flashcards_generator`: **pass**, 53 source files.
- `uv run ruff check .`: **pass**.
- `uv run ruff format --check .`: **fail**, 2 unformatted files.
- `uv run task quality-gate`: **fail**, 3 complexity violations listed above.
- `uv build`: **pass**.
- `git diff --check`: **pass**.
- Final process scan found no currently live product/NotebookLM process; this does not cover quit during blocked production login or delete.

## Blockers before approval

1. Add typed `cancel_management()` delegation to `ApplicationServices`/`WorkflowServices`, make app teardown reach and reap the real NotebookLM management subprocess, and cover the production composition path rather than a stronger fake.
2. Make cancellation-time generation cleanup bounded and externally stoppable, including a test where notebook deletion actually blocks and is then terminated/reaped.
3. Make `ruff format --check` and the configured project quality gate exit zero, then replace stale completion receipts with command-backed results.
