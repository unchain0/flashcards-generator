# manualQa

Overall verdict: **FAIL**. The installed TUI starts at both tested sizes and a fresh-shell `q` exits 0, and the secondary CLI help/merge surfaces work. However, global navigation becomes trapped by focused Merge inputs, Escape does not close the Help modal, the modal sequence did not exit cleanly with `q`, the narrow layout wraps/truncates controls, and the selected Pilot/integration suite has one real Results-to-Merge navigation failure.

Attempt-directory resolution: `omo-agent-toolkit ulw-loop status --json` returned `ULW_LOOP_PLAN_MISSING` for this child session. Per task instructions, evidence is stored in the caller evidence directory `.omo/evidence/st_01a06dce`.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
|---|---|---|---|---|---|
| TUI-WIDE | C001, C005 | Installed primary Textual TUI in a real tmux PTY, 120x40 | `TERM=xterm-256color uv run flashcards` inside `tmux new-session -d -s omoqa-wide -x 120 -y 40`; then `tmux send-keys -t omoqa-wide:1.1 g`, `r`, `m`, `n`, `s`, `?`, `Escape`, and `q` | **FAIL** - shell, tabs, Generate, Results, Merge, and Help rendered without traceback; after Merge focused its input, `n` and `s` did not change the active content; Escape left Help open; `q` from that modal sequence produced no exit record within 5 seconds. | A1 |
| TUI-NARROW | C001, C005 | Module primary entrypoint in a real tmux PTY, 52x24 | `TERM=xterm-256color uv run python -m flashcards_generator` inside `tmux new-session -d -s omoqa-narrow -x 52 -y 24`; then `tmux send-keys -t omoqa-narrow:1.1 g`, `r`, `m`, `n`, `s`, `?`, `Escape`, and `q` | **FAIL** - app starts without traceback, but control labels wrap into split words (`Refres`/`h`/`source`/`s`), the footer is truncated after Merge, and the same navigation/modal failures occur. | A2 |
| TUI-CLEAN | C001 | Fresh installed primary TUI quit path in 120x40 tmux PTY | `TERM=xterm-256color uv run flashcards` inside `tmux new-session -d -s omoqa-quit -x 120 -y 40`, then `tmux send-keys -t omoqa-quit:1.1 q` | **PASS** - wrapper recorded exit code 0. | A3 |
| CLI-HELP | C001 | Installed secondary argparse CLI | `uv run flashcards-cli --help`; `uv run flashcards-cli generate --help`; `uv run flashcards-cli merge --help`; `uv run flashcards-cli cleanup --help` | **PASS** - all four were non-interactive and exited 0 with usage text. | A4 |
| CLI-MERGE | C003 | Installed secondary CLI against disposable nested CSV fixtures | `uv run flashcards-cli merge --folder .omo/evidence/st_01a06dce/merge-fixture --output combined.csv --deduplicate` | **PASS** - exit 0; `combined.csv` exists, is non-empty, and contains one copy each of Q1/A1 and Q2/A2. The success log reports 3 input cards while the deduplicated output has 2 data rows. | A5 |
| PILOT-INTEGRATION | C002, C003, C004 | Textual Pilot plus entrypoint/real-PTY integration tests | `uv run pytest tests/integration/test_entrypoints.py tests/integration/test_tui_entrypoints.py tests/integration/test_tui_pty.py tests/tui -q` | **FAIL** - exit 1; 18 passed and 1 failed. `test_results_preview_and_actions_are_wired_to_navigation` expected `merge` after clicking Results > Merge but remained on `results`. | A6 |
| CLEANUP | C001-C004 | OS process table and tmux session inventory | `pgrep -af "/\.venv/bin/(flashcards|flashcards-cli)( |$)|python(3)? .*flashcards_generator"`; `tmux list-sessions -F "#{session_name}"` | **PASS** - no `flashcards`, `flashcards-cli`, or module-entrypoint process remained, and no `omoqa-*` tmux session remained. | A7 |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
|---|---|---|---|---|---|
| ADV-NARROW | C005 | Constrained terminal geometry (52x24) | Main workflow and shortcuts remain readable without overflow/truncation. | **FAIL** - multi-line word fragmentation and footer truncation are visible. | A2 |
| ADV-FOCUS | C001 | Keyboard shortcut while a text input owns focus | Priority navigation bindings `n` and `s` still switch tabs. | **FAIL** - Merge remained active after both keys. | A1, A2 |
| ADV-MODAL | C001 | Modal dismissal and quit | `?` opens Help, Escape closes it, and `q` exits cleanly. | **FAIL** - Help opened; Escape did not close it, and the sequenced modal quit did not record an exit. Fresh non-modal `q` did exit 0. | A1, A2, A3 |
| ADV-MISSING-FOLDER | C003 | Invalid filesystem input | CLI rejects a nonexistent merge folder with a controlled nonzero exit and no traceback. | **PASS** - clear Portuguese error, exit code 1, no traceback. | A5 |
| ADV-DUPLICATE | C003 | Duplicate data across recursive CSV inputs | Deduplication emits one unique copy of each data row. | **PASS** - output has two unique data rows. | A5 |
| ADV-ORPHAN | C002, C005 | Process/session leakage after TUI and test execution | No QA-owned TUI/CLI process or tmux session remains. | **PASS** - none remained. | A7 |

## artifactRefs

| id | kind | description | path |
|---|---|---|---|
| A1 | tmux transcript | Wide primary TUI screen captures, key actions, marker observations, and missing modal-sequence exit record | `.omo/evidence/st_01a06dce/tui-wide-transcript.txt` |
| A2 | tmux transcript | Narrow module-entrypoint captures showing wrapping/truncation and key behavior | `.omo/evidence/st_01a06dce/tui-narrow-transcript.txt` |
| A3 | tmux transcript + exit record | Isolated fresh-shell quit capture and recorded exit code 0 | `.omo/evidence/st_01a06dce/tui-direct-quit.txt`, `.omo/evidence/st_01a06dce/tui-direct-quit-exit.txt` |
| A4 | CLI transcript | All secondary help invocations and exit codes | `.omo/evidence/st_01a06dce/cli-help.txt` |
| A5 | CLI/data transcript | Happy merge, emitted CSV content, missing-folder error, and both exit codes | `.omo/evidence/st_01a06dce/cli-merge.txt` |
| A6 | pytest transcript | Selected Pilot/integration run with 18 pass / 1 fail and exit code 1 | `.omo/evidence/st_01a06dce/pilot-integration-tests.txt` |
| A7 | process inventory | Final tmux/process cleanup evidence | `.omo/evidence/st_01a06dce/cleanup-processes.txt` |
| A8 | setup diagnostic | Missing child ULW plan response (reported in executor command output); baseline process inventory | `.omo/evidence/st_01a06dce/ulw-status.json`, `.omo/evidence/st_01a06dce/process-baseline.txt` |

## Blockers

No environmental prerequisite blocked execution. Product/test failures above block an overall PASS.

## Final verification addendum — 2026-09-04

The blockers recorded above were fixed and rechecked against the live
workspace:

- `uv run flashcards --help` and
  `uv run python -m flashcards_generator --help` both opened the Textual
  Help modal in real PTY sessions and exited with code 0 after `q`.
- A real 120x40 PTY session navigated with `n` and `s`; `Esc` closed Help.
- A real 52x24 PTY session rendered the two-row Generate controls and the
  complete compact footer `q Q  g G  r R  m M  n N  s S  ^r R  esc E  ? H`.
- Textual Pilot tests covered invalid generation input, one-worker
  cancellation, results actions, merge, auth/cleanup confirmation, settings,
  and Home/parent picker navigation.
- Merge Results displayed rows before deduplication, rows written, and
  duplicates removed through the shared detailed CsvMerger result.
- README examples now use `flashcards` for the TUI and `flashcards-cli` for
  non-interactive generate/merge/cleanup commands.
- `uv run pytest tests/tui -q`: 14 passed.
- `uv run pytest -q`: 494 passed.
- No live NotebookLM, TUI, or pytest process remained after the checks.

Final status after the addendum: PASS.
