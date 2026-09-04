# Independent Security / Concurrency Code Review

## Scope and evidence

- Task: independent read-only audit of subprocess control, paths/symlinks, state durability and resume races, untrusted JSON/PDF handling, secret leakage, resource consumption, and dependency/config changes.
- ULW status could not provide an attempt directory: `ULW_LOOP_PLAN_MISSING`. Per the required fallback, this report is at `.omo/evidence/st_01a0601d-code-review.md`.
- Reviewed the working-tree diff and the current implementations. Existing evidence was treated as untrusted; this review did not rely on a claimed success report.
- No live NotebookLM authentication or calls were made.

## Result

**Security/concurrency audit: FAIL**  
**Confidence: high**  
**codeQualityStatus: BLOCK**  
**recommendation: REQUEST_CHANGES**

## Skill-perspective check

Ran: yes. I loaded and applied `remove-ai-slops` and `programming`.

- `remove-ai-slops`: violated. The diff grows an unused legacy client and adds a configuration-string test that only mirrors a CI implementation literal.
- `programming`: violated. Several touched Python modules greatly exceed the 250 pure-LOC ceiling, and the new test is brittle implementation/config mirroring rather than a meaningful behavioral seam. The legacy client also duplicates the active adapter process boundary.

## Findings

### CRITICAL

None.

### HIGH

1. **Timeout cleanup does not contain LibreOffice descendants.**
   - Evidence: `src/flashcards_generator/infrastructure/pdf_utils.py:82-96`, timeout handling at `:113-115`.
   - `subprocess.run()` is invoked without an isolated session/process group. On timeout, Python kills and reaps only the direct `soffice` process; converter children can survive. A crafted PPTX can therefore leave background processes consuming CPU, memory, or files after the advertised 120-second deadline.
   - The active NotebookLM adapter correctly creates a new session and signals the group (`adapters/notebooklm_adapter.py:74-118`), but that guarantee is absent from this other untrusted-document subprocess path.
   - Required fix: run LibreOffice in its own process group/session and terminate then kill/reap that group on timeout. Add a focused test that proves the group-control path, rather than only `TimeoutExpired` return handling.

2. **Untrusted document/response inputs have no resource bounds; semantic boundary detection materializes a dense quadratic matrix.**
   - Evidence: unbounded PDF parsing/page extraction at `src/flashcards_generator/infrastructure/semantic_chunker.py:109-125`; dense `cosine_similarity(tfidf_matrix)` at `:147-149`; unbounded raw artifact read/JSON parse at `src/flashcards_generator/adapters/notebooklm_adapter.py:347-350` and `:391-397`; same legacy client behavior at `src/flashcards_generator/infrastructure/notebooklm_client.py:232-238`.
   - The pair cap recently added to `QualityFilter` does not protect `SemanticChunker`: `cosine_similarity` allocates an N-by-N dense result before the boundary scan. A PDF with a very large page count can exhaust memory. The downloaded JSON response is also read and parsed entirely without byte, card-count, field-length, nesting, or parser-work limits. These are direct denial-of-service paths for local input and remote CLI output.
   - Required fix: impose file/response and card/text bounds before parsing, close readers deterministically, and avoid dense all-pairs similarity (or impose a strict segment ceiling before vectorization). Add adversarial size/segment-limit tests.

### MEDIUM

1. **Final deck publication is not atomic and is unlocked when resume is disabled.**
   - Evidence: lock selection only applies when `request.resume` is true at `src/flashcards_generator/application/use_cases.py:253-280` and `:421-434`; final write is direct truncate-and-write at `src/flashcards_generator/application/exporter.py:30-37`.
   - Two default (non-resume) invocations targeting the same output can both pass the pre-existing-file check and concurrently truncate/write the same CSV. Readers can observe partial CSV and one run can overwrite the other. Resume mode protects the active run, but no-resume is the default.
   - Required fix: use a per-output lock for every generation mode and publish CSV through a same-directory atomic replacement. Test with independently held lock ownership or a deterministic write barrier.

2. **The diff expands a duplicate, unsafe subprocess boundary instead of routing through the active adapter.**
   - Evidence: `src/flashcards_generator/infrastructure/notebooklm_client.py:27-48` uses `subprocess.run` without process-group cleanup; repository guidance explicitly calls this legacy client debt in `src/flashcards_generator/infrastructure/AGENTS.md:52`.
   - This client receives substantial new code in the diff, despite having neither the adapter's `start_new_session` nor its group termination/reaping behavior. It creates a second divergent security contract for command execution.

3. **Useless/brittle configuration-mirroring test.**
   - Evidence: `tests/unit/test_coverage_edge_cases.py:22-28` reads `.github/workflows/ci.yml` and asserts the literal `--cov-fail-under=80`.
   - This does not exercise shipped behavior and merely duplicates a requested CI setting. It is an overfit/slop test under both required skill perspectives; CI itself executes the machine-consumed configuration.

4. **Touched modules are far beyond the required 250 pure-LOC ceiling, reducing reviewability of safety-critical code.**
   - Evidence: current pure-LOC measurements: `application/use_cases.py` 1488, `pdf_utils.py` 542, `semantic_chunker.py` 464, `adapters/notebooklm_adapter.py` 523, `notebooklm_client.py` 242 (near ceiling).
   - The new safety logic was added into already oversized modules, mixing path validation, resume lifecycle, network orchestration, and cleanup. This is a maintainability and regression-risk concern, not a claim that every line is incorrect.

### LOW

None.

## Verified controls

- The active adapter uses list argv, `shell=False`, a new process session, process-group TERM/KILL fallback, and reaps the leader: `src/flashcards_generator/adapters/notebooklm_adapter.py:74-118`.
- Its command-error logs use status/output lengths rather than response contents: `src/flashcards_generator/adapters/notebooklm_adapter.py:120-125`, `:194-208`.
- Resume manifests/results use private modes, `O_NOFOLLOW`, same-directory temporary files, `os.replace`, and file/directory fsync: `src/flashcards_generator/infrastructure/chunk_state_repository.py:104-151`. The nonblocking flock is correctly held across resume processing in resume mode.
- Input snapshots use no-follow descriptors and a checked, private snapshot directory: `src/flashcards_generator/application/use_cases.py:315-430`.

## Safe verification run

- `git diff --check` and targeted Ruff: PASS.
- `uv run mypy` on the six security-relevant changed source files: PASS (`Success: no issues found in 6 source files`).
- Focused, no-network tests: PASS, 149 tests in 1.10s:
  `test_chunk_state_repository.py`, `test_notebooklm_adapter.py`, `test_notebooklm_client.py`, `test_pdf_utils.py`, `test_semantic_chunking.py`, `test_use_cases_edge_cases.py`, and `test_use_cases_resume.py`.

The passing tests do not exercise live NotebookLM and do not cover the high-severity subprocess-descendant or bounded-resource cases.

## Residual risks

- Filesystem symlink checks are substantially stronger than the prior path handling, but pathname validation plus later open remains subject to hostile same-user directory replacement races; descriptor-relative traversal from a trusted root would further narrow that window.
- `notebooklm-py` was upgraded from 0.7.3 to 0.8.1 in `pyproject.toml`/`uv.lock`. This audit verified lockfile hashes and local code only; it did not authenticate, install from the network, or independently review the upstream release/changelog.

## Blockers

1. Isolate and clean up the LibreOffice process group on timeout.
2. Add enforced bounds for untrusted PDF/JSON processing and eliminate/guard dense semantic similarity allocation.
