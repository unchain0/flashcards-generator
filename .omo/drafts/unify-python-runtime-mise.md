---
slug: unify-python-runtime-mise
status: approved
intent: clear
pending-action: write .omo/plans/unify-python-runtime-mise.md
approach: Migrate every project runtime authority to Python 3.10, install NotebookLM and Playwright in the project environment, remove isolated-tool discovery, fix zsh-specific mise activation, and verify with TDD plus full automated gates.
---

# Draft: unify-python-runtime-mise

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
runtime | One Python 3.10 project environment owns app, NotebookLM, Playwright, Radon, and dev tooling | active | pyproject.toml:1-106; .python-version:1; uv.lock:1-3
tooling | CI, Ruff, mypy, pre-commit, docs, and agent guidance agree on Python 3.10 | active | .github/workflows/ci.yml:25-143; .pre-commit-config.yaml:83-91; AGENTS.md:7-72
paths | NotebookLM resolves from the active project PATH without isolated uv-tool fallbacks | active | src/flashcards_generator/infrastructure/paths.py:7-38; tests/unit/test_paths.py:8-55
shell | zsh activates mise with zsh output and starts without `(eval):unset` | active | /home/avell/.zshrc:35; official mise docs
cleanup | Temporary debug and login-session artifacts are removed without touching unrelated `.omo/` state | active | .debug-journal.md; tmux session notebooklm-login

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->
Python policy | `>=3.10,<3.11` and `.python-version` `3.10` | preserves a single minor-version environment and satisfies NotebookLM/Playwright floors | yes
Dependency placement | `notebooklm-py[browser]==0.7.3` and direct `playwright==1.61.0` runtime dependencies | both CLIs are invoked by the shipped workflow and must resolve reproducibly in `.venv` | yes
Browser setup | document and execute `uv run playwright install chromium`; remove the isolated install task | `uv sync` installs Python packages, while browser binaries remain an explicit post-sync asset | yes
Git behavior | no staging or commits | user did not explicitly request commits; `.omo/` must remain untracked | yes

## Findings (cited - path:lines)
- `pyproject.toml:6,43,71,94-106` pins Python 3.9 across package, Ruff, mypy, and an isolated Python 3.14 NotebookLM task.
- `.python-version:1`, `.github/workflows/ci.yml:59-78`, and `uv.lock:3` are additional Python 3.9 authorities.
- `src/flashcards_generator/infrastructure/paths.py:18-36` searches PATH, an isolated uv-tool directory, and `~/.local/bin`; project-local execution only needs PATH plus command fallback.
- `README.md:21-28,146-152`, `AGENTS.md:12,41,68-72`, and `src/flashcards_generator/infrastructure/AGENTS.md:59-81` document the split 3.9/3.14 or isolated uv-tool model.
- `/home/avell/.zshrc:35` evaluates `mise activate bash`; `/home/avell/.bashrc:26` correctly uses bash activation.
- PyPI metadata for `notebooklm-py==0.7.3` and `playwright==1.61.0` declares `Requires-Python >=3.10`; NotebookLM's browser extra accepts Playwright `>=1.40,<2`.
- Completed compatibility research found Python 3.10, 3.11, and 3.12 all support the current locked dependency family; it preferred 3.12 as a modern default, while confirming 3.10 needs no dependency upgrades. Python 3.13/3.14 would require NumPy/SciPy and possibly scikit-learn upgrades, so they remain out of scope. The user's approved 3.10 decision is still valid and controlling.
- Official mise docs specify `eval "$(~/.local/bin/mise activate zsh)"` in `.zshrc`; evaluating that output in a fresh zsh exited 0.
- Completed mise research confirms `_mise_add_prompt_command` is bash activation machinery, while zsh uses `add-zsh-hook`; the exact unset symptom also existed in an older mise cleanup bug. The durable fix is one zsh activation line, with duplicate activation/version checks only if the symptom persists.
- Dirty baseline: modified `README.md` and `pyproject.toml`; untracked `.debug-journal.md` and `.omo/`. Existing tracked hunks add Chromium installation to the isolated setup and must be superseded without losing that capability.

## Decisions (with rationale)
- User approved Python 3.10 only (`>=3.10,<3.11`) and TDD.
- NotebookLM and Playwright move into project dependencies; no uv-tool or Python 3.14 environment remains.
- Playwright is pinned directly because its CLI is an explicit operational dependency; Chromium installation remains explicit and project-env-scoped.
- Path tests must demonstrate red before removing isolated fallback behavior, then green after the minimal implementation.
- `.zshrc` receives a one-line shell-specific fix only; `.bashrc` is already correct and remains unchanged.
- Existing behavior, Clean Architecture boundaries, NotebookLM authentication state, and all unrelated shell/repository files remain unchanged.

## Scope IN
- `pyproject.toml`, `.python-version`, `uv.lock`.
- `src/flashcards_generator/infrastructure/paths.py` and `tests/unit/test_paths.py`.
- `.github/workflows/ci.yml`, `.github/workflows/pre-commit.yml`, `.pre-commit-config.yaml` only where runtime enforcement or trigger coverage requires alignment.
- `README.md`, root `AGENTS.md`, and `src/flashcards_generator/infrastructure/AGENTS.md` runtime/install/path references.
- `/home/avell/.zshrc:35` only.
- Conditional removal of `.debug-journal.md` and tmux session `notebooklm-login`.

## Scope OUT (Must NOT have)
- No Python 3.11+ target, dependency-wide opportunistic upgrades, application refactor, architecture change, or unrelated documentation rewrite.
- No changes to `/home/avell/.bashrc`, authentication storage, browser profile data, or unrelated shell aliases/environment variables. Playwright may write only its expected browser binaries under `~/.cache/ms-playwright/`, whose before/after inventory must be captured.
- No deletion, staging, or committing of `.omo/`; no commit or push without a separate explicit request.
- No weakening or deletion of tests, linting, type checks, security hooks, or CI jobs.

## Open questions
None. The user approved the recommended approach and selected TDD.

## Approval gate
status: approved
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
Approved decisions: Python 3.10 only; project-local NotebookLM/Playwright; zsh-specific mise activation; TDD.
Pending action: finish `.omo/plans/unify-python-runtime-mise.md` and offer execution or optional high-accuracy review.
