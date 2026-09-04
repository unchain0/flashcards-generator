# CLI and quality audit

**Scope and method.** Static audit of the requested CLI/composition, metadata,
workflow, documentation, and test files only; no production or test command was
run because doing so would load code outside the read-only scope. Severity is
based on user-visible outcome. Line numbers refer to the working tree.

## Findings

### 1. HIGH - `cleanup` accepts conflicting and invalid destructive selectors
**Location:** `src/flashcards_generator/interfaces/cli.py:127-142,310-322`.

**Observed mechanism:** `--days` and `--all` are independent flags rather than
a required mutually-exclusive group. When both are present, the truthy `days`
branch silently wins and ignores `--all`. `type=int` also accepts negative
values; zero is accepted by argparse but then reported as if no selector had
been supplied because `if args.days` is false.

**Impact:** `flashcards cleanup --all --days 7` looks like a request to delete
all notebooks but deletes only seven days' worth. Negative values are passed to
the adapter. These are unsafe or misleading outcomes for a destructive command.

**Reliable RED scenario:** Add parser tests asserting `SystemExit(2)` for
`cleanup --all --days 7`, `cleanup --days 0`, and `cleanup --days -1`; all three
currently parse, and the conflict executes the days path. The zero invocation
currently returns 1 from `run()` rather than a parse error.

**Smallest safe fix:** Put the two selectors in an argparse
`add_mutually_exclusive_group(required=True)` and make `--days` use a local
positive-integer converter. Retain the existing runtime branch as a defensive
fallback.

**Verification:**
`uv run pytest tests/unit/test_cli_cleanup.py -q`

### 2. MEDIUM - directory arguments accept regular files
**Location:** `src/flashcards_generator/interfaces/cli.py:190-195,331-335`.

**Observed mechanism:** Both `generate --input-dir` and `merge --folder` only
check `Path.exists()`, although their help text requires a directory. A regular
file passes validation and is handed to the downstream use case/merger.

**Impact:** A copy/paste or shell-variable mistake produces downstream failures
(or a traceback) rather than a clear CLI validation error and exit status 1.

**Reliable RED scenario:** Create `input.pdf`, call
`CLI()._validate_input(input_pdf)`, and assert false; it currently returns true.
Likewise, mock `CsvMerger.merge`, run `merge --folder input.pdf`, and assert the
merger is not called; it is currently called.

**Smallest safe fix:** Require `path.is_dir()` in both guards and retain the
existing error/return-1 behavior.

**Verification:**
`uv run pytest tests/unit/test_cli.py tests/unit/test_cli_merge.py -q`

### 3. MEDIUM - authentication and language subprocess results are either lost or falsely reported
**Location:** `src/flashcards_generator/interfaces/cli.py:175-188,201-204,208-220`.

**Observed mechanism:** Authentication requires a literal `"✓"` in stdout in
addition to exit code zero and converts timeout/executable-not-found into an
undifferentiated `False`. The caller only reports "Não autenticado". Language
setup uses `check=False`, ignores its return code/stdout/stderr, and logs
"Idioma configurado" even when the command failed.

**Impact:** A compatible NotebookLM version whose successful auth output lacks
the glyph prevents generation; users cannot distinguish missing executable,
timeout, and invalid login. An invalid language or failed language command is
reported as successful, so generated content may use an unexpected language.
This is particularly material while the user-preserved NotebookLM dependency
upgrade is under review.

**Reliable RED scenario:** Mock auth to return `returncode=0, stdout="Authenticated"`
and assert authentication succeeds; it currently fails. Separately mock language
setup to return `returncode=2, stderr="unsupported language"` and assert no
success log is emitted; it currently logs success.

**Smallest safe fix:** Treat auth process exit status as authoritative and log a
bounded reason for timeout, missing executable, and nonzero exit. Inspect the
language return code and log a warning (including bounded stderr) on failure;
do not log success unless it is zero.

**Verification:**
`uv run pytest tests/unit/test_cli.py tests/unit/test_coverage_edge_cases.py -q`

### 4. MEDIUM - ordinary subprocess OS errors escape the CLI boundary
**Location:** `src/flashcards_generator/interfaces/cli.py:179-188,212-220,303-329`.

**Observed mechanism:** The auth and language subprocess wrappers catch only
`TimeoutExpired` and `FileNotFoundError`; `PermissionError` and other `OSError`
instances propagate. Cleanup does not catch adapter subprocess exceptions at
all. Only `KeyboardInterrupt` is converted to a CLI status in generate.

**Impact:** Permission failures, broken executable paths, or adapter process
errors expose a Python traceback instead of a clear error and documented
nonzero exit status. This leaves auth and cleanup operational failures poorly
observable.

**Reliable RED scenario:** Mock `subprocess.run` in `check_auth` with
`PermissionError("denied")`, or mock cleanup's `delete_all_notebooks` with that
exception, then assert `CLI.run()` returns 1 and logs the operation context;
the current calls raise.

**Smallest safe fix:** Catch `OSError` alongside `TimeoutExpired` at the direct
subprocess boundaries, and catch/report the adapter's expected process failure
at the cleanup command boundary before returning 1. Preserve
`KeyboardInterrupt`/130.

**Verification:**
`uv run pytest tests/unit/test_cli.py tests/unit/test_cli_cleanup.py -q`

### 5. MEDIUM - README commands and environment configuration do not match the parser
**Location:** `README.md:48-53,124-135`; `src/flashcards_generator/interfaces/cli.py:63-106,147-159`.

**Observed mechanism:** README uses `merge ./output/Tema1`, but the parser
requires `--folder`/`-f`; the positional argument is rejected. README also
advertises `FLASHCARDS_TIMEOUT`, `FLASHCARDS_INPUT_DIR`, and
`FLASHCARDS_OUTPUT_DIR`, while the parser has a fixed timeout default, requires
`--input-dir`, and has only a fixed output default. No CLI code reads these
environment variables.

**Impact:** Both documented merge examples exit with argparse status 2. Users
who configure the advertised variables still get a missing-input error or the
hard-coded timeout/output values.

**Reliable RED scenario:** `CLI().parser.parse_args(["merge", "./output/Tema1"])`
raises `SystemExit(2)`. With the three variables set,
`parse_args(["generate"])` still raises status 2; supplying input still yields
timeout 900 rather than the environment value.

**Smallest safe fix:** Change the two merge examples to `merge --folder
./output/Tema1` and remove the unsupported environment-variable section. If
environment configuration is intended product behavior instead, implement it
and add parser-level tests.

**Verification:**
`uv run python -m flashcards_generator merge --folder ./output/Tema1 --help`

### 6. LOW - unreachable default-generation branch and duplicate root launchers obscure the supported entry point
**Location:** `src/flashcards_generator/interfaces/cli.py:364-370`,
`main.py:1-5`, `src/flashcards_generator/__main__.py:1-6`, and
`pyproject.toml:20-21`.

**Observed mechanism:** The parser creates `input_dir` only on the `generate`
subparser, so when `command is None` the `hasattr(args, "input_dir")` condition
is always true and the apparent "default to generate" path is unreachable.
`main.py` and `__main__.py` are duplicate five/six-line wrappers, while the
installed `flashcards` script directly targets `interfaces.cli:main`.

**Impact:** The comments promise a default that cannot occur; three launch
surfaces must stay synchronized and README promotes only the un-packaged root
wrapper rather than the installed command.

**Reliable RED scenario:** Mutate the final `return self._run_generate(args)`
to any value; `CLI().run()` with no subcommand still returns 1 from the prior
help branch. Delete the call in either launcher and existing scoped tests still
pass (the package test imports but never executes the `__main__` guard).

**Smallest safe fix:** Replace the no-command branch with help plus return 1,
remove its default-generation comments, and select one documented launcher:
the installed `flashcards` script plus `python -m flashcards_generator` are
sufficient. If `main.py` must remain, make it the only documented development
wrapper and add execution coverage.

**Verification:**
`uv run pytest tests/unit/test_cli_cleanup.py tests/unit/test_main_entry.py -q`

### 7. MEDIUM - CLI tests leave real subprocess behavior and key request wiring unverified
**Location:** `tests/unit/test_cli.py:116-148,207-247`;
`tests/unit/test_coverage_edge_cases.py:254-293,562-587`;
`tests/AGENTS.md:27-28,59-64`.

**Observed mechanism:** Generate success tests skip auth but do not patch
`_set_language`; they execute the real `find_notebooklm`/`subprocess.run` path.
This contradicts the suite rule that unit tests isolate subprocesses. The
custom-options test asserts only that a use case was constructed, not the
request it receives. The coverage test replaces parser output with a
`MagicMock`, bypassing argparse's command, malformed-argument, and option
wiring.

**Impact:** Tests can depend on the developer's installed NotebookLM command
(and could change its language setting). Regressions that discard `--timeout`,
`--difficulty`, `--quantity`, include/exclude/files, or generate exit behavior
can pass despite nominal CLI coverage.

**Reliable RED scenario:** Mutate `_create_request` to always use timeout 900
or omit `include_pattern`; `test_run_with_custom_options` still passes. Put a
sentinel subprocess side effect in `test_run_success`; it proves the current
test reaches a real subprocess seam.

**Smallest safe fix:** Patch `_set_language` in all generate command tests;
assert the actual `GenerateFlashcardsRequest` passed to `execute` for every
option. Add parser tests for missing required arguments, unknown subcommand,
invalid choice, invalid timeout, and invalid cleanup selector, asserting
`SystemExit(2)`.

**Verification:**
`uv run pytest tests/unit/test_cli.py tests/unit/test_coverage_edge_cases.py -q`

### 8. MEDIUM - module entry-point test never executes the entry point, and CI does not enforce coverage
**Location:** `tests/unit/test_main_entry.py:17-36`,
`src/flashcards_generator/__main__.py:5-6`,
`.github/workflows/ci.yml:79-87`, and `pyproject.toml:61-70`.

**Observed mechanism:** The test's own comments acknowledge that importing
`__main__` does not execute its guard; it never asserts `mock_main` was called.
CI produces coverage reports but specifies no `--cov-fail-under` and the
pytest configuration has no coverage threshold. Codecov upload failures are
explicitly nonfatal.

**Impact:** Deleting `main()` from the `python -m` guard can pass tests. Large
coverage regressions also remain green in CI, even though the test file claims
to target 100% coverage.

**Reliable RED scenario:** Temporarily remove `main()` at
`__main__.py:6`; both entry-point tests still pass. Temporarily add an untested
branch or remove a CLI test; the CI pytest command still exits zero because it
only reports coverage.

**Smallest safe fix:** Execute the module with `runpy.run_module` under
`run_name="__main__"` and assert the patched function was called (or use a
bounded subprocess test of `python -m ... --help`). Set an agreed coverage
floor in pytest config or add `--cov-fail-under=<baseline>` to CI, and make
coverage publication non-optional if it is a merge signal.

**Verification:**
`uv run pytest tests/unit/test_main_entry.py -q && uv run pytest --cov=flashcards_generator --cov-fail-under=<baseline> -q`

## Preservation note

`git diff -- pyproject.toml uv.lock` shows pre-existing user changes that must
be preserved: `notebooklm-py[browser]` is pinned from `0.7.3` to `0.8.1` in
`pyproject.toml:9` and the corresponding package metadata, version, sdist, and
wheel hashes are updated in `uv.lock`. Both files also have an existing mode
change from `100755` to `100644`.

## Verify checklist

- **Subcommands and statuses:** generate (0/1/130), cleanup (0/1 and destructive
  selectors), merge (0/1), no-command (1), and argparse malformed-input status
  2 were reviewed; gaps are findings 1, 2, 6, and 7.
- **Auth/subprocess/logging:** authentication output, timeout/not-found/OS-error
  handling, language nonzero results, and cleanup process errors are covered by
  findings 3 and 4.
- **Docs/packaging/CI:** merge and environment drift (5), launch surfaces and
  installed script (6), workflow dependency/runtime selection and coverage
  enforcement (8) were reviewed. CI obtains Python from `.python-version` while
  metadata requires Python `>=3.10,<3.11`; its content was out of scope, so no
  mismatch is asserted.
- **Dead code, duplication, test quality, observability:** addressed in 3, 4,
  6, 7, and 8. No code or test changes were made.
