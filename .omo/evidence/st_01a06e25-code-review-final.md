# Final code review: Textual migration

## Verdict

- **codeQualityStatus:** BLOCK
- **recommendation:** REQUEST_CHANGES
- **result:** FAIL

The migration is broadly functional and the targeted automated checks are green, but the final tree does not yet satisfy the complete objective. Shutdown does not own all TUI-started workers/processes, persisted generation settings are not fully honored, and the required completion/manual evidence is not in a reproducible final state.

## Skill-perspective check

No skill-loading interface was available in this child task. I applied the `programming` and `remove-ai-slops` criteria supplied in the task directly to production code and tests. The diff **violates both perspectives**: it introduces a domain-to-application type dependency, leaves lifecycle ownership incomplete, contains tests that block/poll rather than await the exact state transition, includes a tautological test and a backup test file, and gathers all historical output CSVs instead of the current run's outputs.

## Findings

### CRITICAL

None.

### HIGH

1. **TUI shutdown does not cancel or reap NotebookLM management work.** `NotebookLMPanel` starts login/auth/language/cleanup as thread workers (`src/flashcards_generator/interfaces/tui/screens/notebooklm.py:98-125`), while `FlashcardsApp.on_unmount()` only signals the generation panel (`src/flashcards_generator/interfaces/tui/app.py:138-146`). Production login uses `subprocess.run(..., timeout=None)` (`src/flashcards_generator/interfaces/composition.py:150-159,182-199`), so quitting during browser login can leave the worker and child process alive indefinitely. Generation cleanup has a related gap: notebook deletion is deliberately uncancellable (`src/flashcards_generator/adapters/notebooklm_adapter.py:543-550`), and unmount does not wait for that cleanup to finish. The current cancellation test uses immediate mocks and does not cover a blocked cleanup or quit-during-login path.

2. **Persisted settings are not faithfully applied to generation.** `Settings.resume` is editable and persisted, but `GeneratePanel._request_from_form()` never sets `resume`, so a saved `false` silently becomes the DTO default `true` (`src/flashcards_generator/infrastructure/settings.py:14-26`; `src/flashcards_generator/interfaces/tui/screens/generate.py:186-205,281-305`). The Settings screen also reconstructs `Settings` without `include_pattern` or `exclude_pattern`, clearing existing persisted filters whenever any setting is saved (`src/flashcards_generator/interfaces/tui/screens/settings.py:31-54,84-108`). Existing Pilot coverage checks language/directories/timeout only and therefore misses this user-visible contract failure (`tests/tui/test_notebooklm_settings_picker.py:166-186`).

3. **The objective's durable completion state and manual evidence remain incomplete.** The canonical goal is still `status: "pending"` (`.omo/ulw-loop/goals.json`, `goals[0].status`). C005 requires raw xterm.js wide/narrow evidence exercising happy, invalid, and cancellation behavior; the checked artifacts are prose screen receipts, while the automated PTY test only waits for `q Q`, checks `Input directory`, and quits (`tests/integration/test_tui_pty.py:49-90`; `.omo/ulw-loop/evidence/C005-pty-wide-screen.txt`; `.omo/ulw-loop/evidence/C005-pty-narrow-screen.txt`). The manual QA artifact retains an original FAIL body followed by an unlinked prose PASS addendum (`.omo/evidence/st_01a06dce/G001-migrate-home-avell-projects-unchain0-manual-qa.md`). This does not meet the objective's explicit reproducible completion condition.

### MEDIUM

1. **Results can present stale/unrelated CSV files.** `UseCaseGenerationWorkflow` recursively scans the entire output directory after a run and labels every CSV as that run's output (`src/flashcards_generator/interfaces/composition.py:92-121`). Existing merged files and outputs from prior runs can therefore appear in Results, and `ResultsPanel` selects the first one for copy/open. This is unnecessary broad production data extraction under the slop review; generated paths should come from the generation operation itself.

2. **Desktop actions report success after command failure.** Both helpers use `check=False` and return `True` without inspecting `returncode` (`src/flashcards_generator/infrastructure/desktop_actions.py:10-28,31-44`). The Results UI can display “CSV copied” or “Open command sent” even when `wl-copy`, `xclip`, or `xdg-open` exits nonzero.

3. **The domain port depends on an application-layer concrete type.** `FlashcardGeneratorPort` imports `application.contracts.CancellationToken` under `TYPE_CHECKING` and exposes it in the domain API (`src/flashcards_generator/domain/ports/flashcard_generator.py:13-17,46-50`). It avoids a runtime cycle but still reverses the intended domain/application dependency direction. Use a domain-owned protocol or bind cancellation outside the domain port.

4. **Several migration tests provide weak or nondeterministic confidence.** The async NotebookLM Pilot test calls blocking `threading.Event.wait(5)` directly on the event-loop thread (`tests/tui/test_notebooklm_settings_picker.py:93-109`); the PTY exit helper polls in timed intervals (`tests/integration/test_tui_pty.py:38-46`); and `test_results_model_preserves_cards_for_preview` only assigns and rereads entity fields without touching Results (`tests/tui/test_results_merge.py:99-104`). These violate the requested exact-event testing discipline. They are MEDIUM rather than HIGH because the targeted suite currently passes and adjacent tests cover part of the behavior.

### LOW

1. `tests/unit/test_generation_progress_cancellation.py.orig` is an untracked backup copy, and `.debug-journal.md` still lists itself for cleanup. These are repository slop and should not ship.
2. The touched implementation remains concentrated in very large modules (`application/use_cases.py` is 2,006 lines; `adapters/notebooklm_adapter.py` is 684 lines; TUI Python totals 1,426 lines). The new screens are reasonably separated, so this is residual maintenance risk rather than a migration blocker by itself.

## What passed

- `uv run ruff check .` — pass.
- `uv run ruff format --check .` — pass, 144 files formatted.
- `uv run mypy src/flashcards_generator` — pass, 52 source files.
- `git diff --check` — pass.
- Targeted migration/regression run — **130 passed in 42.85s** across TUI, entrypoints, PTY, cancellation, workflows, settings, CSV merge, CLI, cleanup, and NotebookLM adapter tests.
- Installed NotebookLM CLI confirms `language get/list/set` exists; the language command wiring is valid.
- README and entrypoint routing are consistent: primary commands launch Textual and non-interactive examples use `flashcards-cli`.
- Detailed merge counts are calculated in the application merger and propagated through the workflow DTO rather than recomputed in the UI.

## Blockers before approval

1. Give every TUI-started worker/subprocess a bounded cancellation and reaping path on quit, including login and cancellation-time notebook deletion, with an exact lifecycle regression test.
2. Preserve and apply all persisted generation settings, especially `resume`, `include_pattern`, and `exclude_pattern`, and test the resulting real `GenerateFlashcardsRequest`.
3. Produce current raw C005 evidence for both required terminal sizes and happy/invalid/cancel actions, then mark the canonical ULW objective complete only after that evidence is truthful.

## Residual risks

The targeted suite is green, but no live authenticated generation or quit-during-browser-login scenario was run in this review. The broad output-directory CSV scan and unchecked desktop command statuses remain likely user-facing correctness issues even after the three approval blockers are addressed.
