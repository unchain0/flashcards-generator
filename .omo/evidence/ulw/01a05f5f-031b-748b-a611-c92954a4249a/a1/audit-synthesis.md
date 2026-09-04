# Evidence-backed implementation map

## Basis, ranking, and boundaries

This is a deduplicated implementation map, not a claim that fixes were made. I re-read the cited current production locations on 2026-09-02. `Verified` means the mechanism is directly present in that code; `Risk` means its failure threshold or external lifecycle needs confirmation. Source links refer to the six input audits: [D](audit-domain-dto.md), [A](audit-application.md), [P](audit-pdf-semantic.md), [S](audit-state-path.md), [N](audit-adapter-client.md), and [C](audit-cli-quality.md).

| Rank | Behavior (lane) | Severity / confidence | Source evidence | User impact |
|---|---|---|---|---|
| 1 | Explicit input containment (L2) | High / Verified | [A A1](audit-application.md#A1), [S F1-F2](audit-state-path.md#f1---high-explicit_files-permits-lexical-traversal-outside-both-roots); `application/use_cases.py:295-303,342-375,380-393` | `../outside.pdf`, a symlink swap, or an in-tree `.txt` can be processed; output can escape its selected root. |
| 2 | Resume state trust, recovery, and exclusive ownership (L2) | High / Verified | [A A2](audit-application.md#A2), [S F3-F8](audit-state-path.md#f3---high-checkpoint-writes-follow-attacker-controlled-symlinks-and-a-predictable-temporary-name); `application/use_cases.py:214-217,473-495,566-577`; `infrastructure/chunk_state_repository.py:28,48-66` | A shared output tree can corrupt another writable file, inject foreign cards, lose concurrent checkpoints, or make every resume fail. |
| 3 | PDF chunk correctness and invalid-document boundary (L4) | High / Verified | [P F1-F3](audit-pdf-semantic.md#f1--high--fixed-size-pdf-chunks-have-incorrect-overlap-and-an-extra-tail-chunk-verified-defect); `infrastructure/pdf_utils.py:105-112,131-135,153-195,300-423,434-440` | Duplicate/tiny chunks, omitted front matter, false page labels, or unhandled corrupt-PDF errors cause incorrect or failed generation. |
| 4 | Semantic chunk preservation and limits (L4) | High / Verified | [P F6-F8](audit-pdf-semantic.md#f6--high--semantic-chunks-can-exceed-max_tokens-verified-defect); `infrastructure/semantic_chunker.py:167-209` | Model requests exceed their limit; short/boundary text disappears; citations identify the wrong page. |
| 5 | Adapter process, status, and response boundary (L5) | High / Verified | [N 1,3,5](audit-adapter-client.md#1-high---adapter-timeout-leaks-the-child-process); `adapters/notebooklm_adapter.py:66-85,168-170,199-246,316-344`; `infrastructure/notebooklm_client.py:30-64,103-111,152-184` | Timed-out CLI processes survive; failed generation can be treated as a valid artifact; valid-but-wrong JSON crashes a batch. |
| 6 | Background completion marker (L2) | High / Verified | [A A3](audit-application.md#a3---background-generation-writes-an-empty-csv-that-permanently-suppresses-retry); `application/use_cases.py:182-189,982-989,1057-1061` | `--no-wait` creates an empty CSV, and every later run skips the source. |
| 7 | CLI destructive selector validation (L6) | High / Verified | [C 1](audit-cli-quality.md#1-high---cleanup-accepts-conflicting-and-invalid-destructive-selectors); `interfaces/cli.py:127-142,310-322` | `cleanup --all --days 7` silently does something narrower than the apparent destructive request. |
| 8 | Generation/export data integrity (L3) | Medium / Verified | [D 3](audit-domain-dto.md#3-anki-tsv-serialization-permits-unescaped-record-and-field-delimiters), [A A4-A7](audit-application.md#a4---duplicate-cards-are-exported-for-non-chunked-documents); `domain/entities.py:56-84`; `application/use_cases.py:596-625,1034-1061`; `converter.py:230-260,332-353`; `csv_merger.py:58-74`; `exporter.py:40-54` | Broken Anki records, duplicates, non-cloze cards, and silently discarded merge columns reach users. |
| 9 | Adapter retries, deadlines, cancellation, and secrets (L5) | Medium / Verified | [N 2,4,7-10](audit-adapter-client.md#2-medium---keyboardinterrupt-cleanup-can-leave-a-zombie-or-descendant-processes); `notebooklm_adapter.py:57-83,122-157,172-222,249-307`; `notebooklm_client.py:24-37,68-127` | Permanent failures wait unnecessarily, requested timeouts are not respected, Ctrl-C leaves children, and prompts/stderr reach logs. |
| 10 | CLI operational truthfulness and test seams (L6) | Medium / Verified | [C 2-8](audit-cli-quality.md#2-medium---directory-arguments-accept-regular-files); `interfaces/cli.py:175-220,303-370`; `README.md:48-53,124-135`; `tests/unit/test_cli.py:116-148,207-247`; `tests/unit/test_main_entry.py:17-36`; `.github/workflows/ci.yml:79-87` | Bad directories/OS errors become tracebacks, failed language setup looks successful, docs do not run, and CI/tests miss regressions. |
| 11 | Normal-generation and chunk retry recovery (L2) | Medium / Verified | [A A4,A8](audit-application.md#a8---chunk-retry-is-narrowly-typed-and-transient-non-runtimeerror-failures-bypass-retrystate-reporting); `application/use_cases.py:690-761,851-859,925-934,1034-1055` | Normal PDFs retain duplicates; transient I/O skips retry and resumable failure reporting. |
| 12 | PPTX, PDF resource lifetime, and large-deck scale (L4) | Medium / Risk | [P F4-F5,F9-F10](audit-pdf-semantic.md#f4--medium--pptx-conversion-accepts-a-stale-output-as-a-fresh-success-verified-defect); `pdf_utils.py:52-78,300,400-410,418,450-470`; `semantic_chunker.py:317-331` | A stale conversion is accepted; handles/chunks can persist; 20k-card similarity analysis can exhaust memory. |
| 13 | Contract hygiene (L1) | Medium/Low / Verified | [D 1,2,4,5](audit-domain-dto.md#1-generationresult-cannot-be-constructed-from-a-clean-import); `domain/ports/flashcard_generator.py:10-29`; `domain/entities.py:65-84`; `application/dto/{generate_request,merge_request}.py:1-18` | A public result model fails on clean import; invalid thresholds and output paths silently do surprising work. |
| 14 | Logging behavior (L4) | Medium/Low / Verified/Risk | [P F11-F12](audit-pdf-semantic.md#f11--medium--the-configured-logger-name-is-ignored-and-redirected-sinks-receive-forced-ansi-verified-defects); `logging_config.py:22-43` | Non-TTY logs contain ANSI and labels are wrong; foreign-sink removal is an embedding-process risk. |

## Falsifiable behavior map

Each row contains one deterministic RED proof and one GREEN proof. Tests subscribe/inspect the exact call, output, or state; none need fixed sleeps. `L#` maps to the disjoint lanes below.

| ID / lane | Exact evidence and user-facing rule | RED now | GREEN after change |
|---|---|---|---|
| B1 L1 | `domain/ports/flashcard_generator.py:10-29` ([D1](audit-domain-dto.md)):  `GenerationResult.deck` must construct from a clean import. | `uv run python -c "from flashcards_generator.domain.ports.flashcard_generator import GenerationResult; GenerationResult(deck={'name':'audit'})"` raises Pydantic undefined-model error. | Same command asserts `.deck.name == 'audit'` and exits 0. |
| B2 L1 | `domain/entities.py:65-84` ([D2](audit-domain-dto.md)):  threshold is finite and in `[0,1]`. | `Deck(name='d').deduplicate(float('nan'))` returns normally. | Parametrized `-0.01, 1.01, nan` each raises `ValueError` before deck mutation. |
| B3 L1 | `application/dto/merge_request.py:15-18` ([D4](audit-domain-dto.md)):  `output_filename` is one nonempty relative basename. | Pydantic accepts `../outside.csv` and `nested/out.csv`. | Both raise `ValidationError`; default still validates. |
| B4 L2 | `application/use_cases.py:295-303,380-393` ([A A1](audit-application.md), [S F1](audit-state-path.md)):  explicit selection has the same resolved, regular, nonempty, supported, input-root boundary as discovery. | `explicit_files=['../outside.pdf']` invokes processing/escapes output; in-tree `notes.txt` is submitted. | Both produce no generator call/no outside CSV; a confined nonempty PDF is processed. |
| B5 L2 | `application/use_cases.py:342-375` ([S F2](audit-state-path.md)):  the object opened remains the validated input object. | Pause after discovery, replace `a.pdf` with an external symlink, resume; generator sees outside path. | Generator is not called (or reads a descriptor confined to input); output is absent. |
| B6 L2 | `chunk_state_repository.py:61-66` ([S F3,F7,F8](audit-state-path.md)):  state writes are private, no-follow, unique-temp, durable, and one run owns a resume directory. | `state.json.tmp -> victim` changes `victim`; two barrier-synchronized writers make one raise/lose state. | Save rejects symlink and keeps `victim == 'KEEP'`; second run gets documented busy result before remote work; spy sees file and parent `fsync`, modes are `0700/0600`. |
| B7 L2 | `use_cases.py:473-495`; `chunk_state_repository.py:28,48-51` ([A A2](audit-application.md), [S F4-F6](audit-state-path.md)):  manifest entries are derived/validated and bad state regenerates only affected work. | Matching manifest names external/missing/corrupt `result_path`; zero generator calls or resume aborts. Same-size/restored-mtime changed source reuses state. | Foreign/out-of-range/duplicate/bad result is pending and regenerated; malformed manifest is quarantined/restarted; source SHA-256 change regenerates. |
| B8 L2 | `use_cases.py:182-189,1057-1061` ([A A3](audit-application.md)):  only completed artifacts create the CSV completion marker or clean resume state. | Existing no-wait test creates zero-row `file.csv`; second call skips it. | First no-wait creates no CSV and second call is not skipped (background deck remains observable). |
| B9 L2 | `use_cases.py:690-761,851-859` ([A A8](audit-application.md)):  classified transient I/O gets bounded retry and final failure persists `FAILED`. | Stub chunk internal: `OSError` then deck; retry method raises first error. | Exact call sequence is two attempts and deck; exhausted classified failure writes one FAILED state. |
| B10 L2 | `use_cases.py:596-625,1034-1055` ([A A4](audit-application.md)):  normal and chunked decks use the same dedup semantics before export. | Non-chunked generator returns two identical valid cards; CSV parses to two rows. | `csv.reader` reads one row. |
| B11 L3 | `converter.py:230-260,332-353` ([A A5](audit-application.md)):  accepted cloze output contains a valid `{{cN::...}}`. | Long non-keyword answer or `{{cX::answer}}` returns a card without valid marker. | Both convert to `None`; a valid marker survives. |
| B12 L3 | `domain/entities.py:56-59`; `exporter.py:40-54` ([D3](audit-domain-dto.md), [A A7](audit-application.md)):  TSV fields do not create an extra field/record. | Tab/newline card produces more than two fields/one physical data row. | Selected documented policy (reject or normalize to HTML) gives one data row/two fields in both public exporters. |
| B13 L3 | `csv_merger.py:58-74` ([A A6](audit-application.md)):  a nonblank merge input row has exactly two columns. | `['front','back','tag']` returns count 1 and loses `tag`. | Raises `CSVMergeError` naming source and row; quoted valid two-column input is retained. |
| B14 L4 | `pdf_utils.py:420-440` ([P F1](audit-pdf-semantic.md)):  fixed chunk size/overlap obeys `0 <= overlap < size` and each later range advances by `size-overlap`. | 51 mock pages with `(30,5)` yield `[0,30),[20,50),[45,51)`, and `(30,30)` divides by zero. | Exactly `[0,30),[25,51)`; invalid configurations raise `ValueError`. |
| B15 L4 | `pdf_utils.py:305,317,359-366` ([P F2](audit-pdf-semantic.md)):  chapter chunks cover retained prefix and metadata reports source indices. | One bookmark `(5,10)` writes pages 5-9 but says 1-5. | Policy-tested prefix plus chapter coverage has exact writer pages and matching log bounds. |
| B16 L4 | `pdf_utils.py:131-195,300,418` ([P F3](audit-pdf-semantic.md)):  pypdf corruption is a distinct controlled boundary outcome. | `_create_reader` raises `EmptyFileError`; count/outline/chunk path leaks it. | All three follow the chosen documented invalid-document result/domain exception and log it once. |
| B17 L4 | `semantic_chunker.py:167-209` ([P F6-F8](audit-pdf-semantic.md)):  every nonempty source token is emitted, chunk tokens never exceed max, and start page is first represented page. | One 20-token sentence under max 10 yields 20; ten-token default input yields `[]`; boundary `[1]` labels page-3 content 2-3. | Split/merge output preserves sentinel tokens, every count `<= max`, and second range is `(3,3)`. |
| B18 L4 | `pdf_utils.py:52-78,300,418,400-470`; `semantic_chunker.py:317-331` ([P F4-F5,F9-F10](audit-pdf-semantic.md)):  conversion is fresh, readers close, and quality deduplication is bounded/correct under degenerate input. | Preexisting `test.pdf` is accepted after no-op soffice; generator close leaves stream unclosed; stop-word duplicate pair remains; a 20k bounded-memory worker allocates dense NxN. | Per-call temp output must be newly created then moved; close spy fires after generator close; exact duplicate is found on empty vocabulary; block/sparse run stays inside memory cap. |
| B19 L4 | `logging_config.py:22-43` ([P F11-F12](audit-pdf-semantic.md)):  configured component label is rendered and non-TTY has no ANSI; ownership policy is explicit. | Captured non-TTY `get_logger('sentinel')` omits sentinel and contains `\x1b[`. | Captured output has sentinel/no ANSI after `logger.complete()`; separately preserve a host sink unless global takeover is explicitly documented. |
| B20 L5 | `notebooklm_adapter.py:66-85,199-246`; `notebooklm_client.py:178-184` ([N1,N3](audit-adapter-client.md)):  a timeout reaps process(es), nonzero is failure before JSON parse, delete reflects exit status. | `communicate` timeout records no terminate/kill/reap; `(1,'{"task_id":"stale"}','auth')` returns stale ID; client delete returns true for status 1. | Mock process records terminate, bounded wait, optional kill, final wait; generation is typed failure/None; delete is false. |
| B21 L5 | `notebooklm_adapter.py:94-112,168-170,316-344`; `notebooklm_client.py:46-64,103-111,152-176` ([N5,N6,N8](audit-adapter-client.md)):  mapping/envelope/items and nonempty string fields are validated; malformed is distinguishable from `cards: []`. | `null`, `42`, `{'cards':'x'}`, `[1]`, or non-string card fields leak `AttributeError`/Pydantic error or collapse to `[]`. | Both boundaries return/raise the defined response error; only schema-valid explicit `[]` is empty success. |
| B22 L5 | `notebooklm_adapter.py:57-83,122-157,172-222,249-307` ([N2,N4,N7,N10](audit-adapter-client.md)):  operation deadline controls subprocess; retry only classified transient nonzero; cancellation reaps; logs redact content. | `wait(timeout=60)` still has runner deadline 1; auth-denied retries; kill branch has no final wait; prompt sentinel appears in capture. | Per-call deadline includes documented grace; auth gets no retry; kill then wait occurs; capture excludes sentinel but has operation/status/attempt/duration. |
| B23 L5 | Adapter/client command construction differs at `notebooklm_adapter.py:142-157,352` and `notebooklm_client.py:91-102,180-183` ([N9](audit-adapter-client.md)):  one selected CLI dialect is contract-tested. | Fake CLI accepts only selected argv; one boundary fails generate/delete. | Both boundaries pass fake CLI generate/delete argv test; opt-in real help smoke confirms supported syntax. |
| B24 L6 | `interfaces/cli.py:127-142,190-220,303-370` ([C1-C4,C6](audit-cli-quality.md)):  selectors are exclusive/positive, directory args are directories, and OS errors/nonzero language are accurately reported. | Parser accepts `--all --days 7`, zero, negative; a file passes directory check; auth PermissionError escapes; language exit 2 logs success. | Each bad selector is argparse 2; files are rejected with 1; mocked `OSError` returns 1 with context; only zero logs language success. |
| B25 L6 | `README.md:48-53,124-135`; `cli.py:63-106,147-159`; tests/CI at [C5,C7,C8](audit-cli-quality.md#5-medium---readme-commands-and-environment-configuration-do-not-match-the-parser) | README `merge ./output/Tema1` exits 2; custom-options test tolerates discarded timeout/include; importing `__main__` never calls main; coverage has no gate. | Shipped docs invocation parses; request spy has every supplied option and language is mocked; `runpy` entry test observes call; agreed baseline is enforced. |

## Disjoint implementation lanes

No production or test path is assigned to more than one lane. A lane may begin only after its listed dependencies; workers must not widen write scope.

| Lane | Exact production write scope | Exact test write scope | Depends on |
|---|---|---|---|
| L1 Contract/value validation | `domain/ports/flashcard_generator.py`; `domain/entities.py`; `application/dto/generate_request.py`; `application/dto/merge_request.py` | `tests/unit/test_entities.py`; `tests/unit/test_value_objects.py`; `tests/unit/test_use_cases.py` (DTO-only cases) | none |
| L2 Input, generation, resume state | `application/use_cases.py`; `infrastructure/chunk_state_repository.py` | `tests/unit/test_use_cases_edge_cases.py`; `tests/unit/test_use_cases_resume.py`; `tests/unit/test_chunk_state_repository.py`; `tests/integration/test_resume_flow.py` | L1 only if threshold contract changes are used here |
| L3 Conversion and exports | `application/converter.py`; `application/csv_merger.py`; `application/exporter.py` | `tests/unit/test_converter.py`; `tests/unit/test_csv_merger.py`; `tests/unit/test_exporter.py` | L1 for the selected shared TSV policy |
| L4 Document processing and logging | `infrastructure/pdf_utils.py`; `infrastructure/semantic_chunker.py`; `infrastructure/logging_config.py` | `tests/unit/test_pdf_utils.py`; `tests/unit/test_pptx_converter.py`; `tests/test_semantic_chunking.py`; `tests/unit/test_logging_config.py` | none |
| L5 NotebookLM boundaries | `adapters/notebooklm_adapter.py`; `infrastructure/notebooklm_client.py` | `tests/unit/test_notebooklm_adapter.py`; `tests/unit/test_adapter_edge_cases.py`; `tests/unit/test_adapter_list_delete.py`; `tests/unit/test_notebooklm_client.py`; `tests/integration/test_client_integration.py` | L1 response/exception contract decision |
| L6 CLI, docs, entry points, CI | `interfaces/cli.py`; `README.md`; `main.py`; `src/flashcards_generator/__main__.py`; `.github/workflows/ci.yml`; `pyproject.toml` (coverage setting only) | `tests/unit/test_cli.py`; `tests/unit/test_cli_cleanup.py`; `tests/unit/test_cli_merge.py`; `tests/unit/test_coverage_edge_cases.py`; `tests/unit/test_main_entry.py` | L5 for final auth/process error taxonomy |

## Dependency matrix and critical path

`X` means the row must settle its public invariant before the column is finalized.

| From \ To | L1 | L2 | L3 | L4 | L5 | L6 |
|---|---:|---:|---:|---:|---:|---:|
| L1 | - | X | X |  | X |  |
| L2 |  | - |  | X | X | X |
| L3 |  |  | - |  |  | X |
| L4 |  | X |  | - |  |  |
| L5 |  | X |  |  | - | X |
| L6 |  |  |  |  |  | - |

**Critical path:** L1 contract decisions -> L2 explicit-path/state ownership and recovery -> L5 adapter response/process contract -> L6 CLI error presentation and real QA. L4 can proceed in parallel, but its corrupt-document result must be consumed by L2 before end-to-end sign-off. L3 can proceed after the TSV policy in L1 and joins at export QA.

## Proposed real CLI QA (opt-in, isolated)

Use a disposable, non-sensitive document and an authenticated sandbox NotebookLM account; do not run destructive cleanup against a personal account. These commands are proposals, not executed evidence.

```bash
QA_ROOT="$(mktemp -d /tmp/flashcards-qa.XXXXXX)"
mkdir -p "$QA_ROOT/input/topic" "$QA_ROOT/output" "$QA_ROOT/artifacts"
cp /absolute/path/to/non-sensitive-sample.pdf "$QA_ROOT/input/topic/sample.pdf"
uv run python -m flashcards_generator --help | tee "$QA_ROOT/artifacts/module-help.txt"
uv run flashcards generate --input-dir "$QA_ROOT/input" --output-dir "$QA_ROOT/output" --files topic/sample.pdf --timeout 900 | tee "$QA_ROOT/artifacts/generate.log"
uv run flashcards merge --folder "$QA_ROOT/output/topic" --output merged.csv | tee "$QA_ROOT/artifacts/merge.log"
python - <<'PY' "$QA_ROOT/output/topic/sample.csv" "$QA_ROOT/artifacts/csv-shape.json"
import csv, json, sys
with open(sys.argv[1], newline='', encoding='utf-8') as f:
    rows = list(csv.reader(f))
assert rows and all(len(row) == 2 for row in rows)
json.dump({'rows': len(rows), 'columns_ok': True}, open(sys.argv[2], 'w'))
PY
```

Expected artifacts: `$QA_ROOT/output/topic/{sample.csv,merged.csv}`; no completed state under `$QA_ROOT/output/topic/.flashcards_resume/sample/`; `$QA_ROOT/artifacts/{module-help.txt,merge.log,generate.log,csv-shape.json}`. For the background regression, run the same generate command with `--no-wait`, assert **no** `$QA_ROOT/output/topic/sample.csv`, then use the documented retrieval flow once it exists; until such a flow is designed, this is intentionally not a successful end-user completion path. For adapter dialect only, capture `"$(command -v notebooklm)" --help > "$QA_ROOT/artifacts/notebooklm-help.txt"` and run the fake-CLI contract test, never logging credentials/prompts.

## Residual risks, pre-existing items, and out-of-scope decisions

| Item | Status | Handling |
|---|---|---|
| NotebookLM live syntax, auth messages, service timing, and real rate-limit classification | Unverified external dependency ([N scope/limits](audit-adapter-client.md#scope-and-limits)) | Validate only with the opt-in sandbox QA/help artifact; redact all output. |
| Input TOCTOU cannot be fully eliminated by re-resolve alone | Residual security risk ([S F2](audit-state-path.md#f2---high-discovery-validation-can-be-bypassed-by-an-input-swap)) | Prefer descriptor/dir-FD ownership; document residual if platform abstraction cannot provide it. |
| Power loss between data/state writes and filesystem/device durability | Residual after atomic rename ([S F8](audit-state-path.md#f8---medium-rename-is-not-crash-durable-and-checkpoint-privacy-depends-on-umask)) | fsync plus B7 recovery; no test can prove physical media durability. |
| PDF temporary-file cleanup caller ownership | Partly unverified ([P F5](audit-pdf-semantic.md#f5--medium--pdf-readers-and-partial-chunk-files-are-not-lifecycle-safe-verified-leak-cleanup-impact-is-a-hypothesis)) | Define ownership in L4; inspect any caller outside this audit before deleting transferred outputs. |
| 20k-card similarity limit | Verified asymptotic risk, workload threshold unverified ([P F10](audit-pdf-semantic.md#f10--high--similarity-filtering-materializes-quadratic-work-and-memory-verified-resource-risk)) | Set a measured memory budget and benchmark fixture before choosing block size. |
| Logging global-sink removal | Integration hypothesis ([P F12](audit-pdf-semantic.md#f12--low--logging-configuration-removes-sinks-it-does-not-own-hypothesis--integration-risk)) | Preserve foreign sinks unless an explicit application-owned global logging contract is approved. |
| Adaptive 5-second inter-chunk pacing, cancellation manifest preservation, math edge cases | Pre-existing test gaps ([A ledger](audit-application.md#checked-categories-and-test-gap-ledger)) | Keep separate from correctness lanes; add event-based, bounded tests only after a product pacing policy. |
| User changes to `pyproject.toml`/`uv.lock` notebooklm pin and file modes | Pre-existing and out of scope ([C preservation note](audit-cli-quality.md#preservation-note)) | Preserve unchanged; do not combine with coverage configuration without explicit owner review. |
| Product choice: reject vs normalize TSV delimiters; background artifact retrieval UX; global logging ownership; legacy client retirement | Out of scope design choices | Decide before coding the affected lane; tests must encode the selected machine-consumed contract, not prose. |

## Synthesis verification

Re-read complete: every B1-B25 row contains a source-audit link, current `file:line` evidence, user outcome, falsifiable RED proof, and GREEN check. The lane table has disjoint production/test writes, the dependency matrix identifies the critical path, the QA section names commands and artifact paths, and the residual-risk table separates unverified, pre-existing, and out-of-scope work. The report is non-empty and below 8,000 tokens.
