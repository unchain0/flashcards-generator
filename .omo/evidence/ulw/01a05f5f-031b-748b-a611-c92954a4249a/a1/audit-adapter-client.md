# NotebookLM adapter/client boundary audit

## Scope and limits

Static audit of only the requested adapter, legacy client, port/entities, and listed tests. No production or test code was changed. External NotebookLM CLI syntax, authentication, and live service behavior were **locally unverified** because `tests/integration/test_client_integration.py:8-22` patches `subprocess.run`; it is not an external integration test.

Confirmed control: both runners construct an argv list and invoke subprocesses without `shell=True` (`src/flashcards_generator/adapters/notebooklm_adapter.py:66-74`, `src/flashcards_generator/infrastructure/notebooklm_client.py:30-38`). Titles, paths, IDs, and prompts therefore are not shell-expanded. The client also explicitly uses `check=False` and then checks status when requested (`notebooklm_client.py:37-41`).

## Findings

### 1. HIGH - Adapter timeout leaks the child process

- **Evidence:** `src/flashcards_generator/adapters/notebooklm_adapter.py:67-75` starts `Popen` and calls `communicate(timeout=self.timeout)`, but `:76-83` cleans up only on `KeyboardInterrupt`; there is no `TimeoutExpired` cleanup path. `generate_flashcards` catches the propagated timeout at `:244-246`, making the call appear finished while the child can remain alive. Existing `tests/unit/test_notebooklm_adapter.py:179-186` incorrectly raises `TimeoutExpired` from the `Popen` constructor, so it cannot detect this leak.
- **Observed mechanism / impact:** A hung CLI survives an adapter timeout, retains pipe/file descriptors, may continue remote mutation, and accumulates processes over repeated jobs. Wait, list, create, add, download, and delete paths can also leak because all use this runner.
- **Reliable RED:** Return a real or mocked process whose `communicate` raises `TimeoutExpired`; assert `terminate`, bounded `wait`, fallback `kill`, and post-kill `wait` occur before the exception is re-raised. The current implementation fails at the first cleanup assertion. A real helper script that records its PID and sleeps can additionally assert the PID disappears after a 50 ms adapter timeout.
- **Smallest safe fix:** Centralize cleanup in `_run_command`: on `TimeoutExpired`, terminate, wait briefly, kill if needed, then always wait/reap and re-raise. On POSIX, start a new session and signal the process group if the CLI can spawn descendants.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py -q -k 'timeout or run_command'`

### 2. MEDIUM - KeyboardInterrupt cleanup can leave a zombie or descendant processes

- **Evidence:** `notebooklm_adapter.py:76-83` terminates and waits on the graceful branch, but after fallback `process.kill()` at `:82` it does not call `wait()`/`communicate()` again. No new session/process group is created at `:67-74`. Existing tests only assert `terminate`/`kill` calls (`tests/unit/test_notebooklm_adapter.py:330-355`), not reaping or descendant cleanup.
- **Observed mechanism / impact:** Ctrl-C during a stuck CLI can leave the direct child unreaped and any CLI-created descendants running; terminal cancellation is not a reliable stop boundary.
- **Reliable RED:** Make first `wait(timeout=5)` raise, then assert a second `wait()` after `kill()`; it is absent. For process-tree behavior, launch a helper that forks a sleeping child and assert both PIDs vanish after injecting `KeyboardInterrupt` into `communicate`.
- **Smallest safe fix:** After `kill`, unconditionally wait/reap; use a process group/session and terminate/kill that group where supported. Preserve and re-raise `KeyboardInterrupt`.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py -q -k keyboard_interrupt`

### 3. HIGH - Nonzero generation status can be accepted as success; client delete always reports success

- **Evidence:** Adapter `_execute_with_retry` runs with `check=False` (`notebooklm_adapter.py:199`), merely logs nonzero status (`:205-209`), and returns stdout (`:211`); `generate_flashcards` then parses and returns an ID (`:225-240`). Thus partial/error stdout such as `{"task_id":"stale"}` wins over exit status. The edge test explicitly preserves this behavior (`tests/unit/test_adapter_edge_cases.py:60-72`). Separately, client deletion ignores the tuple returned by `_run(..., check=False)` and returns `True` unconditionally (`notebooklm_client.py:178-184`).
- **Observed mechanism / impact:** Failed or partially written generation output can advance workflow with an invalid artifact. A failed legacy-client delete is reported as successful, leaking remote notebooks.
- **Reliable RED:** (a) Mock adapter `_run_command` as `(1, '{"task_id":"stale"}', 'auth failed')` and require generation failure rather than `"stale"`; current code returns the ID. (b) Mock client `_run` as `(1, '', 'denied')` and assert `delete_notebook(...) is False`; current code returns `True`.
- **Smallest safe fix:** Treat return code as authoritative before parsing stdout. Translate nonzero generation into the port's documented failure result/error; return `returncode == 0` from client delete.
- **Verify:** `uv run pytest tests/unit/test_adapter_edge_cases.py tests/unit/test_notebooklm_client.py -q -k 'generate or delete'`

### 4. MEDIUM - Timeout arguments are not the actual subprocess deadline

- **Evidence:** Every adapter command uses only instance `self.timeout` in `communicate` (`notebooklm_adapter.py:57-60,75`). `wait_for_source(timeout=...)` and `wait_for_artifact(timeout=...)` pass the method timeout only to CLI argv (`:122-135`, `:249-264`). Port config exposes `GenerationConfig.timeout_seconds` (`src/flashcards_generator/domain/ports/flashcard_generator.py:22`) but `_build_generate_command`/generation never reads it (`notebooklm_adapter.py:138-157,212-225`). The client has the same split between `_run`'s instance timeout (`notebooklm_client.py:24-37`) and wait argv (`:68-80,114-127`).
- **Observed mechanism / impact:** A requested wait longer than the instance deadline is cut off early; a shorter requested wait may still leave CLI shutdown overhead bounded only by the longer instance deadline. Per-generation timeout configuration has no effect.
- **Reliable RED:** Instantiate with `timeout=1`, call `wait_for_source(..., timeout=60)`, and assert the runner receives an effective deadline greater than 60 (or timeout plus a documented grace); no per-call deadline exists. Set `GenerationConfig(timeout_seconds=7)` and assert generation uses 7; it still uses the constructor value.
- **Smallest safe fix:** Add a per-call runner timeout and pass the operation/config timeout plus a small shutdown grace; define precedence between instance default and per-operation values. Remove the unused timeout field only if the public contract intentionally does not support it.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_notebooklm_client.py -q -k 'timeout or wait'`

### 5. HIGH - JSON is syntax-checked but not shape/type validated

- **Evidence:** Create/add annotate decoded values as `dict` but immediately call `.get` without checking runtime type (`notebooklm_adapter.py:94-112`; `notebooklm_client.py:46-64`). Adapter generation does the same through `_extract_artifact_id` (`notebooklm_adapter.py:168-170,237-240`). Parsing accepts any JSON, then assumes a mapping/list, iterable cards, and mapping items (`notebooklm_adapter.py:316-340`; client equivalent `notebooklm_client.py:155-164,170-176`). Only `JSONDecodeError`/`OSError` are caught (`notebooklm_adapter.py:342-344`; client `:166-168`). `Flashcard` requires string front/back (`src/flashcards_generator/domain/entities.py:45-49`), so validation errors also escape.
- **Observed mechanism / impact:** Valid JSON with the wrong shape (`null`, scalar, `{"cards":"oops"}`, `[1]`, or non-string fields) produces `AttributeError`/validation exceptions instead of a translated boundary failure. This can crash a batch after a successful download.
- **Reliable RED:** Parameterize both parsers with `null`, `42`, `{"cards":"x"}`, `[1]`, and `[{"front":[],"back":"a"}]`; require a documented parse failure/empty result and no raw exception. Parameterize create/generate responses with `[]` and `null`; current `.get` calls fail.
- **Smallest safe fix:** Validate top-level and item shapes explicitly at the boundary, require nonempty string IDs/front/back, and translate schema failures consistently. Prefer a small Pydantic response model or narrow `isinstance` checks.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_notebooklm_client.py -q -k 'parse or response or create or generate'`

### 6. MEDIUM - Empty/missing cards are indistinguishable from malformed output

- **Evidence:** Missing card keys default to `[]` (`notebooklm_adapter.py:316-321`; client `notebooklm_client.py:155-160`), invalid cards are silently skipped (`notebooklm_adapter.py:323-340`; client `:162-176`), and decode/I/O errors also return the initially empty list (`notebooklm_adapter.py:332-344`; client `:152-168`). Existing tests assert empty output as success-like `[]` (`tests/unit/test_notebooklm_client.py:187-205,228-239`; `tests/unit/test_notebooklm_adapter.py:269-282`).
- **Observed mechanism / impact:** A genuinely empty deck, wrong response envelope, all-invalid cards, malformed JSON, and missing file collapse to the same result. Callers cannot decide whether to retry, alert, or publish an empty deck.
- **Reliable RED:** Feed `{}`, `{"cards":[]}`, malformed JSON, and `[{}]`; assert distinct typed parse outcomes/errors. Current methods return `[]` for all four.
- **Smallest safe fix:** Keep `[]` only for a schema-valid explicit empty card array if empty decks are allowed; raise/return a typed parse error for missing envelope, malformed file, and invalid items. Align this in the port contract.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_notebooklm_client.py -q -k parse_flashcards`

### 7. MEDIUM - Retry policy retries permanent failures and has inconsistent second-attempt semantics

- **Evidence:** Download's `is_retryable` includes `or attempt < max_retries - 1` (`notebooklm_adapter.py:283-301`), so every first and second caught command failure is retryable, including invalid arguments and auth denial; it blocks for 30 then 60 seconds (`:302-307`). Generation retries whenever a stderr substring matches, even if return code is zero (`:199-203`), sleeps a fixed 300 seconds (`:186-193`), and runs the retry with default `check=True` (`:193`), so the second failure raises `RuntimeError` past `generate_flashcards`, whose handlers cover only JSON/timeout (`:241-246`).
- **Observed mechanism / impact:** Permanent/auth errors incur avoidable delays; warning text on a successful command can duplicate remote generation; first and second failures produce different caller-visible behavior. Fixed sleeps also make cancellation/retry orchestration opaque (KeyboardInterrupt propagates, but there is no injectable cancellation signal).
- **Reliable RED:** (a) Return auth-denied or invalid-argument status on download and assert no sleep/retry; both currently retry twice. (b) Return `(0, valid_json, "rate limit policy docs")` and assert no retry. (c) First return retry marker, second nonzero; assert one consistent translated failure rather than leaked `RuntimeError`.
- **Smallest safe fix:** Retry only classified transient nonzero failures, use bounded/configurable backoff, keep status handling identical on all attempts, and make waiting cancellation-aware. Never retry startup/config/auth failures.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_adapter_edge_cases.py -q -k retry`

### 8. MEDIUM - Errors and authentication failures have inconsistent translation and may expose stderr

- **Evidence:** Adapter create translates only `RuntimeError` to `GenerationError` (`notebooklm_adapter.py:89-99`), while add lets runner/JSON/startup errors escape (`:101-117`); waits return only bool (`:135-136,264-265`); generation often returns `None` (`:233-246`) but retry failure can escape; list collapses selected errors to `[]` (`:370-403`). Client generation catches every `Exception`, including response-shape/programmer errors, and returns `None` (`notebooklm_client.py:103-111`); `KeyboardInterrupt` is a `BaseException` and correctly propagates. Raw stderr is embedded in `RuntimeError` (`notebooklm_adapter.py:84-85`; client `notebooklm_client.py:39-40`) and logged in full on several paths (`notebooklm_adapter.py:205-209,233-235,375-376`; client `:147-148`).
- **Observed mechanism / impact:** Callers cannot distinguish auth expiry/consent-required, timeout, rate limit, malformed response, startup failure, and empty result. Raw CLI stderr may contain auth URLs, account details, document text, or tokens in terminal/log sinks.
- **Reliable RED:** Make client `_run` return syntactically valid but wrong-shaped JSON and require a typed response failure; current broad handler returns `None`. Return nonzero stderr `"authentication required TOKEN=secret"` across create/add/generate/list and assert one typed auth error plus redacted logs; behavior is currently divergent and secret text is retained/logged.
- **Smallest safe fix:** Define typed boundary errors (auth, timeout/cancel, command failure, response failure), catch only expected exceptions, preserve `KeyboardInterrupt`/`SystemExit`, and redact/summarize stderr with status/operation metadata. **External auth classification/messages remain locally unverified** and must be matched against real CLI outputs without storing credentials.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_notebooklm_client.py -q -k 'interrupt or auth or failure'`

### 9. MEDIUM - Adapter and client encode divergent CLI contracts with no real contract test

- **Evidence:** Adapter generation uses `--notebook` and appends sanitized instructions as an unflagged final argument (`notebooklm_adapter.py:142-157`); client generation uses `-n` and `--prompt` (`notebooklm_client.py:91-102`). Adapter delete is `delete -n ID -y` (`notebooklm_adapter.py:352`), while client delete is `notebook delete ID --force` (`notebooklm_client.py:180-183`). The adapter unit test pins the positional instruction (`tests/unit/test_notebooklm_adapter.py:159-164`), while the only integration test patches subprocess and checks only create (`tests/integration/test_client_integration.py:8-22`).
- **Observed mechanism / impact:** At least one boundary can silently drift from the installed CLI version; prompts may be parsed as unexpected positional arguments and cleanup syntax may fail. Shell injection is not present, but argument correctness is unverified externally.
- **Reliable RED / mutation:** Add an executable fake CLI that records exact argv and rejects any dialect other than the selected supported contract; run identical generate/delete contract cases against both boundaries. Mutating `--prompt` to positional (or vice versa) must fail. Then perform an opt-in real `--help`/sandbox smoke test. Current mocked integration cannot detect either mismatch.
- **Smallest safe fix:** Select one supported CLI version/dialect, share command construction (or retire the duplicate client), and contract-test argv including spaces/newlines/path characters. Do not introduce a shell.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py tests/unit/test_notebooklm_client.py tests/integration/test_client_integration.py -q`

### 10. LOW - Debug observability leaks content while omitting useful structured context

- **Evidence:** Adapter joins and logs the first 200 characters of the full generation argv, including instructions (`notebooklm_adapter.py:217-222`), logs the first 500 characters of stdout/stderr in `_log_command_output` (`:172-184`), and repeats both streams in generation (`:225-230`). Logs lack elapsed time, PID, attempt number on first attempt, timeout used, and structured exit status on success.
- **Observed mechanism / impact:** Flashcard/source content and authentication diagnostics can enter logs, while operators still cannot correlate latency, timeout, retries, or process cleanup. Truncation limits volume but is not redaction.
- **Reliable RED:** Capture logs with prompt/stdout/stderr containing sentinel `SECRET_123`; assert the sentinel is absent while operation, duration, attempt, timeout, and return code fields are present. Current logs expose the sentinel when it falls inside the truncation window.
- **Smallest safe fix:** Never log prompt or response bodies by default; log structured operation metadata, lengths, duration, attempt, timeout, and status, with an explicit redaction function for bounded diagnostic stderr.
- **Verify:** `uv run pytest tests/unit/test_notebooklm_adapter.py -q -k log`

## Verification coverage checklist

- **Argument construction / shell invocation:** confirmed argv/no-shell control; dialect drift in finding 9.
- **Return codes / partial output:** finding 3.
- **Timeouts / leaked processes:** findings 1 and 4.
- **Cancellation / KeyboardInterrupt:** findings 2 and 8.
- **Malformed JSON and wrong JSON shapes:** finding 5.
- **Empty cards:** finding 6.
- **Retries:** finding 7.
- **Authentication failures:** finding 8; real auth flow **locally unverified**.
- **Observability / cleanup:** findings 1, 2, and 10.
