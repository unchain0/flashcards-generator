# Final gate 4: Textual migration

## recommendation

**REJECT**

The tree is substantially functional and all automated project gates are green, but it does not yet satisfy the completion objective. Production management cancellation can race into a new subprocess after cancellation, generation Results can claim unrelated historical CSVs, desktop actions report success for failed commands, and the exact C005 xterm.js evidence is absent. The completion manifest cannot truthfully be marked complete yet.

## originalIntent

Migrate both primary entrypoints to a production-quality Python 3.10 Textual TUI while preserving the non-interactive `flashcards-cli` generate/merge/cleanup surface. Reuse domain/application behavior, keep dependencies one-way, provide real generation/progress/results/merge/NotebookLM/settings/picker/help behavior, make cancellation bounded and leak-free, preserve resume and partial-failure behavior, and finish only with RED-to-GREEN evidence, real wide/narrow terminal QA, green quality gates, and an independent audit.

## desiredOutcome

A user launches either primary command into the same responsive TUI, can navigate every workflow and help surface, generate exactly once with persistent advanced options, safely cancel all generation and NotebookLM work without an orphan process, inspect only the current run's cards/CSV files, receive truthful copy/open status, use the legacy CLI unchanged, and rely on reproducible Pilot/PTY/manual receipts at both required terminal sizes.

## userOutcomeReview

The primary TUI, navigation, settings, picker, merge, CLI compatibility, formatting, typing, complexity, build, and broad regression suite all work in the current tree. The latest `ApplicationServices.cancel_management()` delegation exists and reaches `ApplicationWorkflows`; the domain cancellation boundary now uses `domain.ports.cancellation.CancellationPort`; generation delete commands are token-scoped and reaped; persisted include/exclude/resume settings map into `GenerateFlashcardsRequest`; and current Pilot/PTY tests pass.

The shipped outcome is still incomplete from the user's perspective. Cancellation of one management command is not an operation-level cancellation: a successfully terminated login can continue into `auth_status()` and launch a fresh child after the cancellation request. Results derives CSV paths by scanning the entire output root, so an empty current run exposes a historical CSV. Linux copy/open helpers return success after a nonzero helper exit. Finally, the final terminal artifacts are prose reconstructions/tmux receipts rather than the exact xterm.js wide/narrow evidence required by C005, and the invalid/cancel receipt is a Pilot summary rather than a raw manual surface transcript.

## blockers

### 1. Management cancellation is not operation-scoped and can start a child after cancellation

- **violatedCriterion:** `C004 / objective no-process-leaks and safe NotebookLM cancellation`
- **observation:** A fresh production-boundary probe used real `ApplicationServices -> ApplicationWorkflows -> NotebookLMManagement`. Cancelling an active login reaped that process, but when the terminated command returned success the login flow immediately entered `auth_status()` and started another subprocess; `thread_alive_after_cancel=True`. `cancel_active()` has no operation cancellation state preventing follow-up commands.
- **evidencePointer:** `src/flashcards_generator/interfaces/composition.py:147-202,285-287`; `src/flashcards_generator/application/workflows.py:143-145`; this receipt, section `freshIndependentEvidence` (`management cancellation probe`).

### 2. Completed generation can expose unrelated historical CSV files

- **violatedCriterion:** `C002 Results: completed generation exposes its generated CSV paths/cards`
- **observation:** `UseCaseGenerationWorkflow.generate()` recursively scans every CSV below the output root after execution. A fresh probe with an empty current run and a pre-existing `historical.csv` returned `decks=0`, `completed=0`, and `csv_paths=['historical.csv']`; Results would select that file for copy/open.
- **evidencePointer:** `src/flashcards_generator/interfaces/composition.py:88-124`; `src/flashcards_generator/interfaces/tui/screens/results.py:98-107`; this receipt, section `freshIndependentEvidence` (`stale CSV probe`).

### 3. Results copy/open actions report success when the desktop command fails

- **violatedCriterion:** `C002 Results CSV open/copy actions`
- **observation:** Both desktop helpers ignore `CompletedProcess.returncode` and return `True` after a nonzero exit. A fresh probe with return code 1 produced `copy_text_nonzero=True` and `open_path_nonzero=True`, causing the UI to display success for failed user actions.
- **evidencePointer:** `src/flashcards_generator/infrastructure/desktop_actions.py:9-44`; `src/flashcards_generator/interfaces/tui/screens/results.py:132-168`; this receipt, section `freshIndependentEvidence` (`desktop action probe`).

### 4. The exact required C005 manual artifact is missing

- **violatedCriterion:** `C005: real xterm.js-rendered wide/narrow screenshots/transcript proving the surface and exercising happy, invalid, and cancel actions`
- **observation:** The final wide/narrow files are short prose observation lists, not raw xterm.js screenshots/transcripts. The raw terminal capture available is tmux and covers only startup/quit, while invalid/cancel is a Pilot prose summary. Therefore the exact manual artifact named by the criterion cannot be reproduced from the repository.
- **evidencePointer:** `.omo/evidence/st_01a06dce/tui-wide-final-transcript.txt`; `.omo/evidence/st_01a06dce/tui-narrow-final-transcript.txt`; `.omo/evidence/st_01a06dce/tui-invalid-cancel-final-transcript.txt`; `.omo/evidence/st_01a06dce/tui-direct-quit.txt`; `.omo/ulw-loop/evidence/C005-pty-wide-screen.txt`; `.omo/ulw-loop/evidence/C005-pty-narrow-screen.txt`; `.omo/ulw-loop/goals.json` objective C005 text.

## criterionDisposition

- **C001 - PASS.** Fresh full tests include both primary entrypoints and real PTYs; all secondary argparse help commands independently exited 0. Source and tests cover q/g/r/m/n/s/?/Escape behavior.
- **C002 - FAIL.** Worker/request/progress/cancellation tests pass, but current-run result identity and copy/open outcomes are incorrect (blockers 2 and 3).
- **C003 - PASS.** Real `CsvMerger` delegation, detailed before/written/duplicate counts, Results wiring, and legacy CLI regressions pass.
- **C004 - FAIL.** Persistence, auth/language/confirmation, picker, and the new concrete delegation are present, but operation-level management cancellation can launch follow-up work after cancellation (blocker 1).
- **C005 - FAIL.** The app and PTY test render at 120x40 and 52x24, but the exact required xterm.js happy/invalid/cancel artifact is missing (blocker 4).
- **C006 - PASS.** Full suite, Ruff, format, mypy, lock, build, complexity, and diff checks are green.

## freshIndependentEvidence

- `uv run pytest -q`: **496 passed in 139.99s**.
- Focused cancellation/TUI/entrypoint suite: **49 passed in 26.22s**.
- `uv run ruff check .`: exit 0.
- `uv run ruff format --check .`: exit 0, 147 files formatted.
- `uv run mypy src/flashcards_generator`: exit 0, 53 source files.
- `uv lock --check`: exit 0.
- `uv run task quality-gate`: exit 0, all functions at B(6) or below.
- `uv build --out-dir /tmp/flashcards-final-gate-4-dist`: exit 0; wheel and sdist produced.
- `git diff --check`: exit 0.
- `flashcards-cli`, generate, merge, and cleanup help: each exit 0.
- Final product-process scan: no live Flashcards or NotebookLM process; no tmux server. One pre-existing defunct pytest PID remained and is not a live product process.
- **management cancellation probe:** `active_before_cancel=True`, `process_reaped=True`, but `thread_alive_after_cancel=True`; the follow-up auth subprocess kept the operation alive.
- **stale CSV probe:** empty generation outcome returned `csv_paths=['historical.csv']` from a pre-existing output file.
- **desktop action probe:** mocked helper exit 1 returned `copy_text_nonzero=True` and `open_path_nonzero=True`.

## architectureBoundaryReview

- Domain cancellation now depends only on the domain-owned `CancellationPort`; no Textual/interface import was found under `domain`.
- Textual remains in `interfaces/tui`; workflow orchestration is delegated to shared application/composition services rather than duplicated in widgets.
- **NOTE:** `WorkflowServices` does not declare `cancel_management()`, while `NotebookLMPanel` uses optional `getattr`. This weakens the typed boundary and allowed tests to use interfaces narrower/stronger than production. The concrete production delegation now exists, so this is not a separate blocker beyond blocker 1.
- Existing application-to-infrastructure imports remain in `application/use_cases.py`; they predate this migration and no criterion-specific regression was established from them.

## removeAiSlopsAndProgrammingPass

Applied directly to production code, tests, and the whole working-tree diff.

- The output-root `rglob("*.csv")` is unnecessary broad extraction and creates false current-run data (blocker 2).
- `tests/integration/test_tui_pty.py::_wait_for_exit` still uses unbounded `waitpid(pid, 0)`, and `_read_until(..., b"")` returns after the first read. This is nondeterministic test/lifecycle debt and does not prove bounded teardown. **NOTE** because the criterion-specific manual evidence failure is already blocker 4 and the test passed in this run.
- `tests/tui/test_notebooklm_settings_picker.py::test_unmount_cancels_running_notebooklm_management_worker` uses a fake whose cancellation behavior does not exercise `ApplicationServices` or follow-up commands. It creates false confidence relative to blocker 1.
- `tests/unit/test_generation_progress_cancellation.py.orig` remains as an untracked backup test artifact. **NOTE** (repository slop, not a stated behavioral criterion).
- No deletion-only requested-removal test was found. The new merge/cancellation/settings tests generally assert machine behavior rather than prose, and the domain cancellation protocol is justified by the one-way-boundary requirement.
- The latest code-review report explicitly records both `programming` and `remove-ai-slops` perspectives and identifies overfit/false-confidence cases. Report coverage was checked but did not replace this direct pass.

## completionManifestReview

The canonical flat plan is `.omo/ulw-loop/goals.json`; child-local `omo-agent-toolkit ulw-loop status --json` has no plan, so the fallback evidence location applies. The canonical goal is still `pending`, while its three consolidated criteria are marked pass. It **must not be marked complete** because C002, C004, and C005 fail as detailed above. The task correctly forbids this gate from mutating goal state.

## checkedArtifactPaths

- `.omo/ulw-loop/brief.md`
- `.omo/ulw-loop/goals.json`
- `.omo/ulw-loop/ledger.jsonl`
- `.debug-journal.md`
- `.omo/ulw-loop/evidence/C001-*`, `C002-*`, `C003-*`, `C004-*`, `C005-*`, `teardown-green.txt`
- `.omo/evidence/G001-migrate-home-avell-projects-unchain0-gate-review.md`
- `.omo/evidence/st_01a06e25-code-review-final.md`
- `.omo/evidence/st_01a06e25-code-review-final-2.md`
- `.omo/evidence/st_01a06dce/G001-migrate-home-avell-projects-unchain0-manual-qa.md`
- `.omo/evidence/st_01a06dce/tui-*-final-transcript.txt`
- `.omo/evidence/st_01a06dce/tui-direct-quit.txt`
- Entire tracked/untracked production and test working tree reported by `git status --short`, including all changed files under `src/flashcards_generator` and `tests`.

## exactEvidenceGaps

1. No production-path test proves that cancelling a NotebookLM management operation prevents all follow-up commands and joins the worker.
2. No test proves generation outcomes contain only CSVs created/updated by the current invocation.
3. No test covers nonzero desktop clipboard/opener exit codes.
4. No raw xterm.js artifact at 120x40 and 52x24 exercises the required happy, invalid, and cancellation actions on the current tree.
5. The code-review report predates the latest fixes and remains a FAIL report; this gate independently rechecked those fixes, but there is no separate post-fix PASS code-review artifact.
