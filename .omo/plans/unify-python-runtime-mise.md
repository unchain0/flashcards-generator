# unify-python-runtime-mise - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** One Python 3.10 environment for the application, NotebookLM, Playwright, Radon, and development tooling, plus a clean zsh startup without the mise `(eval):unset` errors.

**Why this approach:** Python 3.10 is the lowest shared supported runtime for the pinned NotebookLM and Playwright releases. Keeping both CLIs inside the project environment removes the split-runtime failure mode and makes local and CI behavior reproducible.

**What it will NOT do:** It will not upgrade the project beyond Python 3.10, refactor application behavior, rewrite unrelated shell configuration, alter saved NotebookLM credentials, or commit/push changes.

**Effort:** Medium
**Risk:** Medium - dependency re-resolution and browser/auth smoke tests cross local tooling boundaries.
**Decisions to sanity-check:** Python stays within the 3.10 minor line; NotebookLM 0.7.3 and Playwright 1.61.0 are pinned in the project; path fallback is PATH-only; tests follow TDD.

Your next move: start work from this plan, or request the optional high-accuracy plan review first. Full execution detail follows below.

---

> TL;DR (machine): Medium effort/risk; unify Python 3.10 dependencies and tooling, remove isolated NotebookLM discovery, fix mise zsh activation, and verify all gates plus real CLI/browser/auth startup.

## Scope
### Must have
- Set every runtime authority to Python 3.10 only: `requires-python = ">=3.10,<3.11"`, Ruff `py310`, mypy `3.10`, `.python-version` `3.10`, CI Python 3.10, and a regenerated lockfile.
- Install `notebooklm-py[browser]==0.7.3` and direct `playwright==1.61.0` in the project environment; install Chromium with `uv run playwright install chromium`.
- Remove isolated uv-tool and explicit `~/.local/bin` discovery from `find_notebooklm()` while retaining PATH resolution and the command-name fallback.
- Use TDD for path behavior and preserve all existing application behavior and architecture boundaries.
- Update only affected install/runtime/troubleshooting documentation and agent guidance.
- Replace `/home/avell/.zshrc:35` with `eval "$(~/.local/bin/mise activate zsh)"`; leave `.bashrc` and all unrelated zsh lines unchanged.
- Preserve the intent of the existing uncommitted Chromium-install hunks while migrating them to the unified environment.
- Modernize only Ruff UP007/UP045 typing syntax required by the Python 3.10 target in the 12 files listed under Todo 5's discovered remediation; no function-body or behavioral refactor.
- Remove `.debug-journal.md` and conditionally close `notebooklm-login`; leave `.omo/` untracked and untouched by product commits.
### Must NOT have (guardrails, anti-slop, scope boundaries)
- No Python 3.11/3.12/3.13/3.14 target, isolated uv tool, broad dependency upgrade, or stale 3.9/isolated-3.14 documentation.
- No application refactor, public CLI behavior change, path hardcoding, type suppression, deleted/weakened tests, or bypassed hooks.
- No changes outside the enumerated project files and `/home/avell/.zshrc:35`; specifically no `.bashrc`, credential, browser-profile, alias, or unrelated dotfile changes. The only allowed cache mutation is Playwright's expected Chromium installation under `~/.cache/ms-playwright/`, with a before/after inventory.
- No staging, commit, amend, push, or deletion of unrelated `.omo/` state.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with pytest. Capture the focused path test failing before implementation and passing afterward; configuration and shell changes use executable assertions and full regression gates.
- Evidence: .omo/evidence/task-<N>-unify-python-runtime-mise.<ext>
- Every command must save stdout/stderr and exit status under `.omo/evidence/`; redact authentication cookies/tokens and never copy the storage-state file.
- Required final gates: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/flashcards_generator`, `uv build`, and `uv run pre-commit run --all-files` must pass. `uv run task quality-gate` must be executed and recorded with its known pre-existing exit 1; the strict B(6) configuration remains unchanged and the exception must never be reported as a pass.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- Wave 1 (parallel foundations): Todos 1-3 establish the path contract, unified dependency graph, and isolated shell correction without overlapping files.
- Wave 2 (parallel alignment): Todos 4-6 align automation/docs, run integrated runtime QA, and clean temporary artifacts after Wave 1.
- Final wave: F1-F4 run independently after all implementation todos and must all approve.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | None | 4, 5 | 2, 3 |
| 2 | None | 4, 5 | 1, 3 |
| 3 | None | 5 | 1, 2 |
| 4 | 1, 2 | F1-F4 | 6 |
| 5 | 1, 2, 3 | F1-F4 | None |
| 6 | 1, 2 | F1-F4 | 4 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Lock NotebookLM path behavior with red-green TDD
  What to do / Must NOT do: First save `git status --short` and `git diff -- pyproject.toml README.md` as the dirty baseline. In `tests/unit/test_paths.py`, replace the tests that expect explicit uv-tool and `~/.local/bin` fallbacks with a test that creates those files while `shutil.which()` returns `None` and expects the literal `notebooklm` fallback. Run that focused test and capture the intentional failure before editing production code. Then simplify `find_notebooklm()` to return `shutil.which("notebooklm") or "notebooklm"`; do not change callers, exception handling, or other path utilities.
  Parallelization: Wave 1 | Blocked by: None | Blocks: 4, 5 | Can parallelize with: 2, 3
  References (executor has NO interview context - be exhaustive): `src/flashcards_generator/infrastructure/paths.py:1-38`; `tests/unit/test_paths.py:1-55`; `AGENTS.md:39-45`; dirty baseline in `.omo/drafts/unify-python-runtime-mise.md`.
  Acceptance criteria (agent-executable): Evidence shows the new focused test fails against old code for the expected obsolete-fallback assertion, then `uv run pytest tests/unit/test_paths.py -v` passes; `paths.py` contains no `Path.home()`, `uv/tools/notebooklm-py`, or explicit `.local/bin/notebooklm` fallback; existing PATH-hit and command-name fallback cases pass.
  QA scenarios (name the exact tool + invocation): Happy—pytest with `shutil.which()` mocked to `.venv/bin/notebooklm` returns that path. Failure—pytest with fake executable files in old fallback locations and `shutil.which()` mocked to `None` returns `notebooklm`, not either file. Evidence `.omo/evidence/task-1-unify-python-runtime-mise.txt`.
  Commit: N | Do not stage or commit; user did not request git writes.

- [x] 2. Move the complete dependency graph to Python 3.10
  What to do / Must NOT do: Preserve the functional intent of the current Chromium-install hunks while replacing the split runtime. In `pyproject.toml`, set `requires-python = ">=3.10,<3.11"`, Ruff `target-version = "py310"`, mypy `python_version = "3.10"`, add `notebooklm-py[browser]==0.7.3` and direct `playwright==1.61.0` to runtime dependencies, and remove the isolated Python 3.14 install task. Set `.python-version` to `3.10`; regenerate `uv.lock` using Python 3.10 and synchronize the environment. Do not opportunistically upgrade unrelated declared constraints.
  Parallelization: Wave 1 | Blocked by: None | Blocks: 4, 5 | Can parallelize with: 1, 3
  References (executor has NO interview context - be exhaustive): `pyproject.toml:1-16,25-37,42-51,70-106`; `.python-version:1`; `uv.lock:1-3`; existing user diff recorded in `.omo/drafts/unify-python-runtime-mise.md`; PyPI contracts: `notebooklm-py==0.7.3` requires Python `>=3.10`, browser extra Playwright `>=1.40,<2`; `playwright==1.61.0` requires Python `>=3.10`.
  Acceptance criteria (agent-executable): `uv lock --python 3.10` and `uv sync --python 3.10` exit 0; `uv run python -c 'import sys; assert sys.version_info[:2] == (3, 10)'` exits 0; `uv.lock` no longer says `==3.9.*` and includes `notebooklm-py` 0.7.3 plus Playwright 1.61.0; no Python 3.14 uv-tool command remains in `pyproject.toml`.
  QA scenarios (name the exact tool + invocation): Happy—`uv run python -c 'import importlib.metadata as m; assert m.version("notebooklm-py") == "0.7.3"; assert m.version("playwright") == "1.61.0"'` passes. Failure—`uv run python -c 'import sys; assert sys.version_info < (3, 11)'` guards against accidental newer-minor resolution. Evidence `.omo/evidence/task-2-unify-python-runtime-mise.txt`.
  Commit: N | Do not stage or commit; user did not request git writes.

- [x] 3. Correct mise activation for zsh without dotfile drift
  What to do / Must NOT do: Change only `/home/avell/.zshrc:35` from `eval "$(~/.local/bin/mise activate bash)"` to `eval "$(~/.local/bin/mise activate zsh)"`. Before and after, capture a narrowly scoped diff/copy of that line; do not modify `.bashrc`, aliases, PATH entries, formatting, or other dotfiles.
  Parallelization: Wave 1 | Blocked by: None | Blocks: 5 | Can parallelize with: 1, 2
  References (executor has NO interview context - be exhaustive): `/home/avell/.zshrc:28-37`; `/home/avell/.bashrc:18-28` (correct bash-only control, read-only); official mise `getting-started.md` and `cli/activate.md` command `eval "$(~/.local/bin/mise activate zsh)"`.
  Acceptance criteria (agent-executable): A before/after comparison proves exactly one `.zshrc` line changed; `zsh -f -c 'eval "$(/home/avell/.local/bin/mise activate zsh)"'` exits 0; a login/interactive zsh emits no `(eval):unset`.
  QA scenarios (name the exact tool + invocation): Happy—run `zsh -lic 'mise --version >/dev/null; python --version'` from the repository and require exit 0. Failure—capture combined output and assert the literal `(eval):unset` is absent; also verify `/home/avell/.bashrc` checksum/content is unchanged. Evidence `.omo/evidence/task-3-unify-python-runtime-mise.txt`.
  Commit: N | External dotfile change is never part of a repository commit.

- [x] 4. Align CI, hooks, documentation, and agent guidance
  What to do / Must NOT do: Update `.github/workflows/ci.yml` so every job uses `.python-version` as the single Python authority, remove the 3.9-only matrix, and include runtime-config files in path triggers where needed. Keep `.github/workflows/pre-commit.yml` using `.python-version`. Strengthen `.pre-commit-config.yaml`'s local version hook to assert Python 3.10 rather than merely print a version. Update only affected Python/NotebookLM/Playwright install and troubleshooting text in `README.md`, Python/dependency statements in root `AGENTS.md`, and the obsolete uv-tool path/dependency examples in `src/flashcards_generator/infrastructure/AGENTS.md`. Do not rewrite unrelated prose or workflow jobs.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: F1-F4 | Can parallelize with: 6
  References (executor has NO interview context - be exhaustive): `.github/workflows/ci.yml:1-143`; `.github/workflows/pre-commit.yml:1-36`; `.pre-commit-config.yaml:83-91`; `README.md:14-29,142-154,164-176`; `AGENTS.md:7-13,39-45,68-72`; `src/flashcards_generator/infrastructure/AGENTS.md:59-81`; final dependency/task shape from Todo 2; final path shape from Todo 1.
  Acceptance criteria (agent-executable): YAML/TOML validation passes; all CI Python setup steps resolve `.python-version`; local pre-commit hook exits nonzero outside Python 3.10 and zero under `uv run`; repository search finds no active Python 3.9 target, isolated Python 3.14 NotebookLM instruction, `uv tool install.*notebooklm`, or old uv-tool lookup outside historical `.omo/` evidence.
  QA scenarios (name the exact tool + invocation): Happy—`uv run pre-commit run check-python-version --all-files` exits 0 and prints/asserts Python 3.10; execute the README setup commands in order. Failure—run the exact hook assertion under a disposable wrong interpreter with `uv run --no-project --python 3.9 python -c 'import sys; expected=(3, 10); actual=sys.version_info[:2]; raise SystemExit(0 if actual == expected else f"Expected Python 3.10, got {actual[0]}.{actual[1]}")'`; require exit 1 and output containing `Expected Python 3.10, got 3.9`. Evidence `.omo/evidence/task-4-unify-python-runtime-mise.txt`.
  Commit: N | Do not stage or commit; user did not request git writes.

- [x] 5. Prove the unified environment through full gates and real smoke tests
  What to do / Must NOT do: Run all focused and full project checks under the synchronized Python 3.10 environment. Before installation, inventory `~/.cache/ms-playwright/` names, sizes, and mtimes without reading browser data. Install Chromium with `uv run playwright install chromium`, capture the after inventory, launch it headlessly once, run project CLI help/module entry points, and run `uv run notebooklm auth check --json`; capture only redacted status output. Do not re-authenticate, expose cookies, mutate notebooks, or call rate-limited generation APIs. Cache changes are allowed only under `~/.cache/ms-playwright/` and must correspond to Playwright's browser installation. Discovered migration remediation: Ruff's Python 3.10 target exposed 43 UP007/UP045 findings; apply only its narrow safe fixer in `src/flashcards_generator/adapters/notebooklm_adapter.py`, `src/flashcards_generator/application/converter.py`, `src/flashcards_generator/application/dto/generate_request.py`, `src/flashcards_generator/application/use_cases.py`, `src/flashcards_generator/domain/entities.py`, `src/flashcards_generator/domain/ports/chunk_state.py`, `src/flashcards_generator/domain/ports/flashcard_generator.py`, `src/flashcards_generator/infrastructure/chunk_state_repository.py`, `src/flashcards_generator/infrastructure/notebooklm_client.py`, `src/flashcards_generator/infrastructure/pdf_utils.py`, `tests/fixtures/adapter_fixtures.py`, and `tests/integration/test_resume_flow.py`; do not change function bodies.
  Parallelization: Wave 2 | Blocked by: 1, 2, 3 | Blocks: F1-F4 | Can parallelize with: None
  References (executor has NO interview context - be exhaustive): `pyproject.toml:94-106` for project tasks; `AGENTS.md:26-30` for entry points; NotebookLM 0.7.3 install contract (`playwright install chromium`, `notebooklm auth check --json`); prior authenticated profile exists at `~/.notebooklm/profiles/default/storage_state.json` but must never be read into evidence.
  Acceptance criteria (agent-executable): `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/flashcards_generator`, `uv build`, and `uv run pre-commit run --all-files` exit 0. `uv run task quality-gate` is executed and exits 1 with `QUALITY GATE FAILED` and `GenerateFlashcardsUseCase._process_large_pdf - D (27)`; this is an explicit documented pre-existing exception, not a pass, and `pyproject.toml`'s B(6) threshold/configuration remains unchanged. Radon over migration-owned `paths.py` and `test_paths.py` introduces no new B(7+) or C-F findings. `uv run playwright install chromium` exits 0; before/after cache inventories exist and show no writes outside `~/.cache/ms-playwright/`; a headless Chromium launch/close exits 0; `uv run python -m flashcards_generator --help` and `uv run flashcards --help` exit 0; redacted auth JSON has `status == "ok"`.
  QA scenarios (name the exact tool + invocation): Happy—run a Python Playwright smoke script that launches Chromium headlessly, creates a blank page, and closes cleanly; run both CLI help forms and auth check. Failure—assert no global `notebooklm` or isolated uv-tool executable is required by invoking through `uv run` with the project environment; fail immediately on any nonzero gate. Evidence `.omo/evidence/task-5-unify-python-runtime-mise.txt` plus redacted `.json` status.
  Commit: N | Do not stage or commit; user did not request git writes.

- [x] 6. Remove temporary artifacts and verify scope preservation
  What to do / Must NOT do: Delete `.debug-journal.md` if present and terminate tmux session `notebooklm-login` if it exists. Compare the final worktree against the recorded baseline, confirm the original Chromium-install intent remains represented in the unified setup, and ensure `.omo/` is neither staged nor deleted. Do not remove auth state, Playwright browser cache, unrelated tmux sessions, or user files; accept only the inventoried Playwright installation changes under `~/.cache/ms-playwright/`.
  Parallelization: Wave 2 | Blocked by: 1, 2 | Blocks: F1-F4 | Can parallelize with: 4
  References (executor has NO interview context - be exhaustive): initial `git status --short` and `git diff -- pyproject.toml README.md` captured by Todo 1; `.debug-journal.md`; tmux target name `notebooklm-login`; `.omo/drafts/unify-python-runtime-mise.md` dirty-worktree findings.
  Acceptance criteria (agent-executable): `.debug-journal.md` is absent; `tmux has-session -t notebooklm-login` returns 1 after conditional cleanup; `GIT_MASTER=1 git status --short` lists only intended product changes plus untracked `.omo/`; `GIT_MASTER=1 git diff --cached --quiet` exits 0; no auth/profile path was modified or deleted, and cache differences are limited to the Todo 5 Playwright inventory.
  QA scenarios (name the exact tool + invocation): Happy—run `rm -f .debug-journal.md; if tmux has-session -t notebooklm-login 2>/dev/null; then tmux kill-session -t notebooklm-login; fi; test ! -e .debug-journal.md; ! tmux has-session -t notebooklm-login 2>/dev/null` twice and require exit 0 both times. Failure—run a Python whitelist check over `GIT_MASTER=1 git status --porcelain` allowing only `README.md`, `pyproject.toml`, `.python-version`, `uv.lock`, `.github/workflows/ci.yml`, `.github/workflows/pre-commit.yml`, `.pre-commit-config.yaml`, `AGENTS.md`, `src/flashcards_generator/infrastructure/AGENTS.md`, `src/flashcards_generator/infrastructure/paths.py`, `tests/unit/test_paths.py`, and untracked `.omo/`; inject one synthetic unexpected path into the parser and require exit 1 with `unexpected path`. Then run `GIT_MASTER=1 git diff --cached --quiet` (exit 0) and `test -d .omo` (exit 0). Evidence `.omo/evidence/task-6-unify-python-runtime-mise.txt`.

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — read this plan and every final diff; verify each Must have and Must NOT have with command-backed evidence. Output `.omo/evidence/final-f1-plan-compliance.md` with `APPROVE` or exact remediation.
- [ ] F2. Code quality review — inspect changed Python/config/docs for correctness, Clean Architecture, type safety, dependency consistency, and test quality; independently rerun focused diagnostics. Output `.omo/evidence/final-f2-code-quality.md` with `APPROVE` or exact remediation.
- [ ] F3. Real manual QA — independently execute Python version, NotebookLM CLI/auth, Playwright headless launch, both application CLI entry points, and clean zsh startup. Output redacted `.omo/evidence/final-f3-manual-qa.md` with `APPROVE` only if every command exits 0.
- [ ] F4. Scope fidelity — compare initial/final status and diffs, verify only approved files changed, `.debug-journal.md`/target tmux session are gone, `.omo/` is unstaged, credentials/browser profiles/unrelated dotfiles are untouched, and any cache delta is limited to the inventoried Playwright Chromium installation under `~/.cache/ms-playwright/`. Output `.omo/evidence/final-f4-scope-fidelity.md` with `APPROVE` or exact remediation.

## Commit strategy
- No commits, staging, pushes, or history edits are authorized by this plan.
- If the user later explicitly requests commits, re-inspect final status/history and create multiple atomic conventional commits, pairing `paths.py` with `test_paths.py`; keep `/home/avell/.zshrc` outside repository commits and exclude `.omo/`.

## Success criteria
- `uv run python` is Python 3.10 and the project/lock/CI/tooling/docs contain no active Python 3.9 or isolated Python 3.14 NotebookLM model.
- NotebookLM 0.7.3, Playwright 1.61.0, Radon, and all project/dev packages resolve from the same synchronized environment.
- Chromium installs and launches via project Playwright; application CLIs and redacted NotebookLM auth check succeed.
- The path TDD red/green evidence exists and all focused/full tests, lint, format, mypy, build, and pre-commit gates pass. The unchanged strict Radon gate is executed and its pre-existing exit-1 complexity debt is explicitly carried to a separate refactor track, never described as passing.
- New zsh startup emits no `(eval):unset`, while `.bashrc` and unrelated dotfile content remain unchanged.
- Temporary debug/tmux artifacts are gone, original user work is preserved in migrated form, and `.omo/` remains untracked/unstaged.
- F1-F4 each report `APPROVE`; results are surfaced for the user's explicit completion acknowledgment.
