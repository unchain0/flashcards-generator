# L6 CLI, documentation, entry point, and CI

## Scope and safety

Implemented B24-B25 only in the assigned L6 paths. No NotebookLM command was
invoked: subprocess boundaries are mocked in unit tests. `main.py` and
`src/flashcards_generator/__main__.py` were inspected and retained; the module
wrapper already called `main()` under its `__name__ == "__main__"` guard. The
entry-point regression was strengthened to execute that guard using `runpy`.

## RED evidence (before production edits)

Full, unmodified transcripts are preserved beside this report.

| Proof | Command and exact result | Transcript |
| --- | --- | --- |
| CLI defects | `uv run pytest tests/unit/test_cli.py tests/unit/test_cli_cleanup.py tests/unit/test_cli_merge.py tests/unit/test_coverage_edge_cases.py tests/unit/test_main_entry.py -q` exited `1`: **9 failed, 59 passed**. The failures covered conflicting/zero/negative cleanup selectors, regular files accepted as input/merge directories, auth `PermissionError` escaping, false language success, cleanup `PermissionError` escaping, and no CI coverage floor. | `L6-red-tests.txt` |
| Auth status | `uv run pytest tests/unit/test_cli.py::TestMain::test_check_auth_uses_successful_exit_status -q` exited `1`: **1 failed** because exit code zero with `stdout="Authenticated"` returned `False`. | `L6-red-auth.txt` |
| README merge syntax | `uv run python -m flashcards_generator merge ./output/Tema1` exited `2`: `error: the following arguments are required: --folder/-f`. | `L6-red-readme.txt` |

The new `runpy` module-entry test was green before production edits because the
existing package wrapper was already correct; no deceptive production change
was made solely to manufacture a failure.

## Changes

- Cleanup now requires exactly one selector and accepts only positive `--days`.
- Generate input and merge folder validation reject regular files.
- Authentication trusts a zero process exit code and reports timeout, missing
  executable, and other OS failures with operation context.
- Language configuration reports success only for a zero exit code and reports
  bounded stderr (or the return code) for failures.
- Cleanup maps adapter `OSError` to a contextual error and exit status 1.
- Generate option wiring is asserted end-to-end at the request passed to the
  use case; generate tests mock language setup and do not execute local
  NotebookLM binaries.
- README documents the installed `flashcards` command and the required
  `merge --folder` syntax; unsupported environment-variable configuration was
  removed.
- CI requires at least 80% coverage and treats Codecov publication failures as
  failures.

## GREEN evidence

| Verification | Exact result | Transcript |
| --- | --- | --- |
| Scoped regression suite | `uv run pytest tests/unit/test_cli.py tests/unit/test_cli_cleanup.py tests/unit/test_cli_merge.py tests/unit/test_coverage_edge_cases.py tests/unit/test_main_entry.py -q` exited `0`: **69 passed in 1.16s**. | `L6-green-tests.txt` |
| Module entry point | `uv run python -m flashcards_generator --help` exited `0` and listed `generate`, `cleanup`, and `merge`. | `L6-green-module-help.txt` |
| Documented merge parser invocation | `uv run flashcards merge --folder ./output/Tema1 --help` exited `0` and accepted `--folder`. | `L6-green-readme.txt` |
| Python diagnostics | LSP reported no diagnostics for `cli.py` and all five changed unit-test files. | tool output |
| Style | Scoped `ruff check` and `ruff format --check` both exited `0`. | terminal output |

## Preservation and final scope check

`git diff -- pyproject.toml` still contains exactly the pre-existing
`notebooklm-py[browser]` `0.7.3 -> 0.8.1` change and its mode change. No
coverage setting was added to `pyproject.toml`; `uv.lock` was not edited by this
lane. The L6 production/interface changes are limited to
`src/flashcards_generator/interfaces/cli.py`, `README.md`, and
`.github/workflows/ci.yml`; tests are limited to the five assigned unit-test
files. Existing unrelated working-tree changes remain outside this lane.
