# L5 NotebookLM adapter/client boundary

## Scope and contract

Implemented the audited adapter/client lane without a live authenticated service.
Both boundaries retain list argv and `shell=False` behavior, use the selected CLI
dialect (`generate flashcards --notebook ... --json [prompt]`; `delete -n ID -y`),
and treat a nonzero exit status as authoritative before parsing output.

`NotebookLMResponseError` is a contextual `RuntimeError` for syntactically valid
but malformed provider responses. It is necessary because the existing domain
errors express generation, source, and download failures but not invalid response
schemas; inheriting `RuntimeError` preserves the existing use-case containment.

## RED then GREEN regression evidence

Each RED command was run after adding its mocked regression tests and before any
production write. The hashes identify the complete terminal captures retained for
this task. Failure excerpts contain assertion behavior only; captured raw process
streams are deliberately not reproduced in this report.

| Behavior | Exact command | RED result | GREEN result |
|---|---|---|---|
| B19 nonzero status | `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_adapter_edge_cases.py tests/unit/test_notebooklm_client.py -q -k 'timeout_reaps or keyboard_interrupt_kill or rejects_nonzero_status or returns_false_for_nonzero_status'` | Shared run: `4 failed, 66 deselected`; stale ID was accepted after status 1 and client delete returned true. SHA-256 `630c140868d1b563342356ac7f97490a6a4931785bd76bb69b64debf5fa7d52d` | Shared run: `4 passed, 66 deselected in 0.11s`. SHA-256 `da6b3b5105521dd8768dce1326ed4512e306a2ce26b32cdcefe551cfc3cd1f3d` |
| B20 deadlines and cleanup | Same command as B19; its mock process preserves communicate timeout, terminate/kill, and final wait semantics. | The same RED had no timeout terminate/reap and lacked the post-kill wait. | The same GREEN proved cleanup/reap, and the separate deadline tests below proved the requested subprocess deadline. |
| B21 response validation | `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_adapter_edge_cases.py tests/unit/test_notebooklm_client.py -q -k 'wrong_json_shape or malformed_envelope'` | `4 failed, 66 deselected`: wrong top-level JSON leaked `AttributeError`; malformed card envelopes either leaked or silently succeeded. SHA-256 `734d027b34d0c4c7099f284f97aab1de41a3fcc90ea4a345c90d9fa997ae90e0` | `4 passed, 66 deselected in 0.08s`. SHA-256 `36b2bf0f9c992f7445c101702fc34962b51e9aa149924f322fd7e96dece3ef12` |
| B22 requested deadlines, retries, and redacted logging | `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_adapter_edge_cases.py tests/unit/test_notebooklm_client.py -q -k 'generation_timeout_is_the_subprocess_deadline or wait_timeout_is_the_subprocess_deadline or does_not_retry or metadata_without_command_output'` | `5 failed, 65 deselected`: generation used the instance deadline; wait used 900 instead of 7; output was logged; permanent auth failure retried; a successful status retried from warning text. SHA-256 `4703a1906c7a9b7ec7002180e252c77d047cac0f5477050cf9c3a06ef74ebd14` | Same command after the focused test name update: `6 passed, 64 deselected in 0.12s`. SHA-256 `f1221e94bb11da7197450ac9fd25ae79ec775586ea944466456b2975a2c8ec2a` |
| B23 shared argv contract | `uv run pytest tests/unit/test_notebooklm_client.py tests/integration/test_client_integration.py -q -k 'adapter_cli_dialect or share_fake_cli_contract'` | `2 failed, 30 deselected`: client rejected the shared task ID / used divergent argv. SHA-256 `9936f76d9ae19e2b699aaed3d992dacf5bd705adabc63d7bca115a965d49486b` | `2 passed, 30 deselected in 0.16s`; an executable fake CLI recorded both generate/delete calls without auth. SHA-256 `e9808ea85613b3f0baa9b02caec7a61f06875036ef73b9ba92eefd767da28d8d` |

## Implementation outcomes

- Adapter subprocesses use the per-operation/configured deadline. Timeout and
  `KeyboardInterrupt` terminate the isolated process group where available, fall
  back to the direct child, and always reap after escalation.
- Adapter generation and client deletion reject nonzero status before consuming
  output. Download retries only classified transient nonzero failures.
- IDs must be nonempty strings. Card payloads must be an explicit array (including
  a valid empty array), and every nonempty card must contain nonempty string sides.
  Wrong envelopes, malformed JSON, unreadable files, and invalid cards raise a
  typed contextual response error.
- Adapter logs metadata only: operation, status, attempt, deadline, and stream
  lengths. It never logs prompts, stdout, stderr, credentials, or identifiers.
  The mocked sentinel regression passed; static review of every adapter/client
  logger call found only fixed messages or metadata/length fields.

## Final verification

```text
uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_adapter_edge_cases.py tests/unit/test_adapter_list_delete.py tests/unit/test_notebooklm_client.py tests/integration/test_client_integration.py -q
96 passed in 0.39s
```

The full terminal capture SHA-256 is
`61d1d36ac53a8b43dcd5fe2a4d6a90dabf6d6602e93d90279406c0f71c1da2e9`.

LSP diagnostics reported no diagnostics for:

- `src/flashcards_generator/adapters/notebooklm_adapter.py`
- `src/flashcards_generator/infrastructure/notebooklm_client.py`
- `src/flashcards_generator/domain/exceptions.py`

`uv run ruff check` on all L5 changed source and test files passed. `git diff
--check` passed. No live NotebookLM authentication or service call was made.

## Secret/stderr review

This report contains no provider stdout, stderr, prompt, token, account, or
credential value. The production logger review found stream length metadata only;
the focused sentinel test proves command output is excluded. Error text includes
only status and stream length, so authentication details and secrets are not
propagated to logs.
