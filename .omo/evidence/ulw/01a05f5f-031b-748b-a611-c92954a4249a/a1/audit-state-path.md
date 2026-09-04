# Filesystem path and resume-state audit

## Scope and threat model

Audited only the requested path/state implementation, port/entity contracts, and listed tests. The adversary can supply `explicit_files`, replace an input entry or write within a shared input/output directory, alter an existing checkpoint, interrupt the process or machine, and run a second invocation for the same source. Relevant assets are the permitted input tree, output tree, checkpoint confidentiality, and correctness of the exported deck.

| Threat | Current result |
|---|---|
| Traversal / explicit input | **Missing protection:** the explicit-files branch neither confines nor validates paths. |
| Input symlinks | **Partial protection:** discovered file symlinks are rejected and resolved paths are checked, but validation is TOCTOU. Explicit files skip both checks. |
| Output/state symlinks | **Missing protection:** repository reads and writes follow parents and the predictable temporary pathname. |
| Malformed JSON | Pydantic rejects it, but the exception is not converted into recoverable stale state. |
| Stale source | Signature invalidates ordinary size/mtime/path changes, but is not a content digest. |
| Interruption | Per-file rename avoids a partially replaced pathname under one writer, but there is no data/metadata fsync or transactional recovery. |
| Concurrent writers | **Missing protection:** shared `.tmp`, no lock/lease, and cleanup races. |
| Permissions | Mode is delegated entirely to the process umask; no private-state requirement is enforced. |

Existing tests demonstrate single-writer replacement-failure preservation, corrupt-JSON rejection, normal stale-signature restart, successful interruption/resume, and symlink rejection only through `_is_safe_file_path`. They do not cover the inputs or interleavings below.

## Findings

### F1 - High: `explicit_files` permits lexical traversal outside both roots

**Evidence:** `src/flashcards_generator/application/use_cases.py:298-300`; output construction at `:389-393`.

**Mechanism:** The explicit list appends `input_path / file_name` after only `exists()` and `is_file()`. Unlike discovery (`:305-314`), it does not call `_is_safe_file_path`. `Path.relative_to()` is lexical, so `input / "../outside.pdf"` yields a relative path containing `..`; `_get_output_subdir` then constructs `output / ..` and creates/uses the output parent.

**User-visible impact:** An explicit `../outside.pdf` is processed even though it is outside the requested input directory, and its CSV/state artifacts can be written beside the configured output directory. This violates source selection and output containment.

**Reliable RED test:** Create `tmp/input`, `tmp/output`, and nonempty `tmp/outside.pdf`; request `explicit_files=["../outside.pdf"]`, stub the generator/chunker to complete, and call `execute`. Current behavior invokes processing and creates `tmp/outside.csv` (or attempts artifacts there). Expected binary result: the source is skipped/rejected and `tmp/outside.csv` does not exist.

**Smallest safe fix:** Apply `_is_safe_file_path(file_path, input_path)` in the explicit branch before appending; resolve the accepted path and derive deck/output paths from that confined resolved path. Reject absolute and any non-descendant path.

**Verification command (after adding the RED test):** `uv run pytest tests/unit/test_use_cases_edge_cases.py -k explicit_files_traversal`

### F2 - High: discovery validation can be bypassed by an input swap

**Evidence:** `src/flashcards_generator/application/use_cases.py:342-375` validates; `:305-309` retains the original pathname; subsequent processing receives that pathname (resume signature begins at `:472`).

**Mechanism:** `_is_safe_file_path` resolves and checks one object, but the later workflow opens the original `Path`. An attacker who can rename in the input tree can replace a checked regular PDF with a symlink to an external PDF after `:375` and before chunking/source upload.

**User-visible impact:** A discovered file named under the input tree can cause an external replacement to be sent to the generator; the output is attributed to the in-tree filename.

**Reliable RED test:** Use a test subclass/monkeypatch to pause immediately after `_find_all_pdfs` returns a checked `input/a.pdf`. Replace `a.pdf` with a symlink to nonempty `outside.pdf`, then continue `execute` with a generator that records the received path and resolves it. Current result resolves outside input. Expected result: no generator/source call for `a.pdf`.

**Smallest safe fix:** Open/operate on a descriptor obtained after confinement (or re-resolve, re-check non-symlink and containment immediately before every use, accepting the residual race only if descriptor APIs are unavailable). Prefer directory-FD operations with `O_NOFOLLOW` for an attacker-writable tree.

**Verification command:** `uv run pytest tests/unit/test_use_cases_edge_cases.py -k input_swap_after_validation`

### F3 - High: checkpoint writes follow attacker-controlled symlinks and a predictable temporary name

**Evidence:** `src/flashcards_generator/infrastructure/chunk_state_repository.py:61-66`; read paths at `:28` and `:51`.

**Mechanism:** `mkdir` follows a symlinked parent; `temp_path` is always `<target>.tmp`; `write_text` follows a pre-existing symlink. Thus a writer in the resume directory can pre-create `state.json.tmp -> victim` and cause the repository to overwrite `victim` before `replace`. Manifest and result reads also follow symlinks. `ChunkStatePort` supplies no rooted-path or ownership contract (`domain/ports/chunk_state.py:18-46`).

**User-visible impact:** In a shared/writable output tree, checkpoint save can corrupt a file writable by the application or expose a JSON-shaped file through resume. A symlinked output ancestor redirects all resume artifacts outside the selected output tree.

**Reliable RED test:** Create a valid resume parent, `victim` containing `KEEP`, and symlink `state.json.tmp` to `victim`; call `save_manifest(parent / "state.json", manifest)`. Current result changes `victim`. Expected binary result: save rejects unsafe state storage and `victim.read_text() == "KEEP"`.

**Smallest safe fix:** Establish a private, trusted resume root once; resolve and verify every state path is beneath it. Use directory-FD APIs, `O_NOFOLLOW|O_CREAT|O_EXCL` for a unique temp file, and `os.replace` relative to that trusted directory FD. Reject symlinked components. Do not make `shutil.rmtree` the containment mechanism.

**Verification command:** `uv run pytest tests/unit/test_chunk_state_repository.py -k symlinked_temp_does_not_modify_victim`

### F4 - High: matching manifests can load an arbitrary result file without semantic validation

**Evidence:** `src/flashcards_generator/application/use_cases.py:477-495`; unbounded fields at `src/flashcards_generator/domain/entities.py:23-29,39-40`; repository follows the supplied path at `infrastructure/chunk_state_repository.py:48-51`.

**Mechanism:** A schema-valid manifest only needs matching signature and total count. Each completed `result_path` is converted directly to `Path` and loaded. It is not required to be the expected `resume_dir/chunk_NNN.json`, below `resume_dir`, unique, or within `1..total_chunks`.

**User-visible impact:** A modified checkpoint can inject cards from any readable valid `Deck` JSON into the exported CSV, skip generation for that chunk, or fail resume by naming a missing/bad file.

**Reliable RED test:** Save a matching manifest for one chunk with `chunk_index=1`, `status=completed`, and `result_path` set to a valid deck JSON outside the resume directory. Run resume with a generator that would produce distinguishable cards. Current result makes zero generator calls and exports the foreign card. Expected result: the foreign path is rejected and chunk 1 is regenerated.

**Smallest safe fix:** Treat result paths as derived data: never trust the serialized path, compute it from validated index; enforce unique indexes in `[1, total_chunks]`, completed-result existence/schema, and card-count consistency. Invalid entries become pending and are regenerated.

**Verification command:** `uv run pytest tests/unit/test_use_cases_resume.py -k resume_rejects_foreign_result_path`

### F5 - Medium: corrupt or interrupted state is terminal rather than recoverable

**Evidence:** `src/flashcards_generator/infrastructure/chunk_state_repository.py:28,51`; unguarded resume loads at `application/use_cases.py:473-475,489-491`.

**Mechanism:** `model_validate_json` raises on malformed JSON; a missing/corrupt completed result also raises. No resume-specific handler quarantines/deletes invalid state and starts that chunk fresh. The existing repository test intentionally proves the exception (`tests/unit/test_chunk_state_repository.py:158-173`).

**User-visible impact:** One truncated, manually edited, wrong-encoding, or partially lost checkpoint prevents automatic resume; through normal `execute`, processing returns no deck and leaves the poison state to fail every retry.

**Reliable RED test:** Write `{invalid json` to the derived state path, invoke `execute(resume=True)` for a chunked source with a successful generator. Current result has no deck and state remains. Expected result: one fresh generation succeeds and the replacement manifest is valid. Repeat with a valid manifest whose completed result file contains `{invalid json`; expected behavior is to regenerate only that chunk.

**Smallest safe fix:** At the resume boundary, catch `OSError`, decode errors, and Pydantic validation errors. Quarantine/remove the invalid manifest; for a bad result, remove only its completed entry/result and regenerate. Log a clear recovery message.

**Verification command:** `uv run pytest tests/unit/test_use_cases_resume.py -k 'corrupt_manifest_recovers or corrupt_result_regenerates_chunk'`

### F6 - Medium: source signature is forgeable/stale for same-size, restored-mtime content

**Evidence:** `src/flashcards_generator/application/use_cases.py:214-217`, comparison at `:477-481`.

**Mechanism:** The signature is `st_size`, floating `st_mtime`, and resolved pathname, not content. Replacing bytes with the same length and restoring the original mtime produces the same signature (or a filesystem with coarse mtime permits it naturally).

**User-visible impact:** Resume reuses completed cards for a changed PDF, producing an export that silently mixes old content with newly processed chunks.

**Reliable RED test:** Persist a completed matching manifest/result, overwrite the source with different bytes of identical length, then restore its original `st_mtime_ns` using `os.utime`. Resume with a generator recording calls. Current result skips the completed chunk; expected result regenerates it.

**Smallest safe fix:** Store a streaming SHA-256 of source bytes (optionally with size and canonical path) and compare it before reuse. Define a versioned signature format so legacy manifests are stale.

**Verification command:** `uv run pytest tests/unit/test_use_cases_resume.py -k source_content_digest_invalidates_resume`

### F7 - High: concurrent writers are neither collision-safe nor transactionally isolated

**Evidence:** `src/flashcards_generator/infrastructure/chunk_state_repository.py:62,65-69`; independent load/save lifecycle at `application/use_cases.py:473-513,566-577`; cleanup at `:279-281`.

**Mechanism:** All writers for one target share `<name>.tmp`. Deterministic interleaving: A writes `state.json.tmp` and pauses before replace; B writes and replaces that same temp with B's manifest; A resumes, its replace raises `FileNotFoundError`. A's successful write is lost. Even unique temp names would not protect the read-modify-write lifecycle or cleanup: two runs load an empty manifest, both perform remote chunk work, and one successful `execute` removes the resume directory while the other is saving its next result/manifest.

**User-visible impact:** Concurrent invocations can fail spuriously, duplicate costly generation, lose a checkpoint, leave orphan state after a CSV is already complete, or make the other run fail during save/load/cleanup.

**Reliable RED test:** Monkeypatch `Path.replace` with two `threading.Event` barriers: block A after its temp write/before replace, let B complete `save_manifest` for the same target, release A. Current result is exactly B's manifest plus A raising `FileNotFoundError`; expected result is a documented busy/lease error before work, with no generation by the second invocation. A second integration test should pause B just before its save and let A finish/cleanup; expected B to be blocked rather than recreate state after success.

**Smallest safe fix:** Acquire a non-blocking exclusive per-resume-dir lock/lease before load and hold it through processing and cleanup; report that the source is already running. Use unique temp files as well, but do not mistake that for lifecycle isolation.

**Verification command:** `uv run pytest tests/unit/test_chunk_state_repository.py tests/integration/test_resume_flow.py -k 'concurrent_manifest_writer or concurrent_resume_is_rejected'`

### F8 - Medium: rename is not crash-durable and checkpoint privacy depends on umask

**Evidence:** `src/flashcards_generator/infrastructure/chunk_state_repository.py:61-66`.

**Mechanism:** `write_text` and `replace` do not fsync the temporary file or parent directory. A power loss can persist the manifest rename but lose the preceding result data, despite logical save ordering at `application/use_cases.py:566-577`. Creation modes are defaults (`mkdir` and `write_text`), so a permissive `umask(0)` makes state directories/files group/world accessible.

**User-visible impact:** After an abrupt machine loss, resume can find a completed record with missing/corrupt result (then hits F5). On a multi-user host, other users can read source paths/cards or modify checkpoints and exploit F4.

**Reliable RED test / mutation scenario:** (1) Unit-test the writer with `os.fsync` spied and require fsync of the temp FD before replace and its parent FD after replace; current implementation makes zero calls. (2) set `umask(0)`, save a manifest, and assert file mode is `0o600` and resume directory `0o700`; current modes retain group/other access. The crash-consistency mutation is binary: simulate loss of the result file after the manifest rename; safe recovery must regenerate, not abort.

**Smallest safe fix:** Create the private resume directory as `0700`, state files as `0600` (also repair existing modes), write/flush/fsync the temp FD, atomically replace, then fsync the containing directory. Combine with F5 recovery for unavoidable storage faults.

**Verification command:** `uv run pytest tests/unit/test_chunk_state_repository.py -k 'fsync or restrictive_permissions'`

## Prioritized remediation

Fix F1/F2 before accepting untrusted inputs; establish an owned no-symlink state root (F3); then add the per-run lease and unique durable writer (F7/F8). Finally validate/recover manifests/results and switch to a content digest (F4-F6). These changes preserve the existing good behavior: normal stale-signature restart, atomic replacement failure preserving the old target, and sequential interruption/resume.
