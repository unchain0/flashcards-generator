# Manual QA matrix

- Goal: `G001-aprimore-e-audite-integralmente-o-pr`
- QA pass: fresh hands-on run against current tree
- Tree stamp: `0f8f3c45e872223eca672497c70b1b529f5dea15` (working tree had pre-existing unstaged changes; no source edits made)
- Provider policy: temporary local fake only; no authentication or live NotebookLM invocation
- Overall verdict: **PASS** for the requested scenarios

## manualQa

### surfaceEvidence

| Scenario | Criterion | Surface | Exact invocation | Verdict | Artifact refs |
|---|---|---|---|---|---|
| QA-01 | C001 | Module CLI help | `.venv/bin/python -m flashcards_generator --help` | PASS; exit 0; help lists `generate`, `cleanup`, and `merge` | A01 |
| QA-02 | C001 | Console-script help | `uv run flashcards --help` | PASS; exit 0; help lists `generate`, `cleanup`, and `merge` | A01 |
| QA-03 | C002 | Real generation CLI with temporary fake provider | `env PATH=/tmp/flashcards-manual-qa.Xnulug/bin:$PATH FAKE_LOG=/tmp/flashcards-manual-qa.Xnulug/provider.log FAKE_MODE=normal .venv/bin/python -m flashcards_generator generate --input-dir /tmp/flashcards-manual-qa.Xnulug/input --output-dir /tmp/flashcards-manual-qa.Xnulug/output --skip-auth-check --timeout 5` (after `command -v notebooklm` and `readlink -f` both resolved to the temporary fake) | PASS; exit 0; one CSV parsed with one nonempty two-column row containing Cloze syntax; provider operation log covered language/create/source add/source wait/generate/artifact wait/download/delete; no traceback | A01 |
| QA-04 | C002 | Provider failure generation CLI | `env PATH=/tmp/flashcards-manual-qa.Xnulug/bin:$PATH FAKE_LOG=/tmp/flashcards-manual-qa.Xnulug/provider.log FAKE_MODE=provider-fail .venv/bin/python -m flashcards_generator generate --input-dir /tmp/flashcards-manual-qa.Xnulug/input-provider --output-dir /tmp/flashcards-manual-qa.Xnulug/output-provider --skip-auth-check --timeout 5` (after mandatory fake-resolution proof) | PASS; exit 1; provider returned status 23, notebook cleanup ran, CSV count 0, resume-state listing empty, no traceback | A01 |
| QA-05 | C002 | Corrupt PDF generation CLI | `env PATH=/tmp/flashcards-manual-qa.Xnulug/bin:$PATH FAKE_LOG=/tmp/flashcards-manual-qa.Xnulug/provider.log FAKE_MODE=corrupt .venv/bin/python -m flashcards_generator generate --input-dir /tmp/flashcards-manual-qa.Xnulug/input-corrupt --output-dir /tmp/flashcards-manual-qa.Xnulug/output-corrupt --skip-auth-check --timeout 5` (after mandatory fake-resolution proof) | PASS; exit 1; contextual source/provider error, notebook cleanup ran, CSV count 0, resume-state listing empty, no traceback | A01 |
| QA-06 | C002 | Merge happy path and parsed auxiliary CSV | `uv run python -m flashcards_generator merge --folder /tmp/flashcards-manual-qa.Xnulug/merge-happy --output merged.csv --deduplicate` | PASS; exit 0; output parsed as one deduplicated two-column Cloze row | A01 |
| QA-07 | C002 | Malformed merge with preexisting output | `uv run python -m flashcards_generator merge --folder /tmp/flashcards-manual-qa.Xnulug/merge-malformed --output merged.csv` | PASS; exit 1 with contextual row-width error; preexisting sentinel output remained byte-identical (41 bytes); no temporary merge files remained | A01 |
| QA-08 | C002 | Missing generation input | `uv run python -m flashcards_generator generate --input-dir /tmp/flashcards-manual-qa.Xnulug/missing-input --output-dir /tmp/flashcards-manual-qa.Xnulug/missing-output` | PASS; exit 1 with `Diretório não existe`; output directory was not created | A01 |
| QA-09 | C002 | Missing merge folder | `uv run python -m flashcards_generator merge --folder /tmp/flashcards-manual-qa.Xnulug/missing-folder --output merged.csv` | PASS; exit 1 with `Pasta inválida ou inexistente` | A01 |
| QA-10 | C002 | Traversal output rejection | `uv run python -m flashcards_generator merge --folder /tmp/flashcards-manual-qa.Xnulug/merge-happy --output ../escaped.csv` | PASS; exit 1 with basename validation error; escaped file was absent | A01 |
| QA-11 | C002/C003 | Disposable filesystem and process cleanup | Shell cleanup trap after all scenarios; post-cleanup checks: `find /tmp -maxdepth 1 -type d -name 'flashcards-manual-qa.*' -print` and `pgrep -x notebooklm` | PASS; fixture root and all temporary QA roots absent; exact `notebooklm` process count 0; cleanup receipt nonempty | A02 |

### adversarialCases

| Scenario | Criterion | Adversarial class | Expected behavior | Verdict | Artifact refs |
|---|---|---|---|---|---|
| ADV-01 | C002 | Missing resource | Controlled contextual error, nonzero exit, no requested output creation | PASS; QA-08 and QA-09 | A01 |
| ADV-02 | C002 | Path traversal | Reject non-basename output, nonzero exit, no escaped file | PASS; QA-10 | A01 |
| ADV-03 | C002 | Malformed CSV / partial-write corruption | Reject malformed input without replacing prior output or leaving temp files | PASS; QA-07 | A01 |
| ADV-04 | C002 | Provider failure | Return nonzero status, clean provider-created notebook, publish no CSV, leave no resume state | PASS; QA-04 | A01 |
| ADV-05 | C002 | Corrupt PDF | Return nonzero contextual failure, publish no CSV, clean notebook and resume state, no traceback | PASS; QA-05 | A01 |
| ADV-06 | C002 | Provider command seam | All generation commands must resolve to the temporary fake before invocation; fake must be the only provider surface | PASS; preflight resolved `command -v` and `readlink -f` to `/tmp/flashcards-manual-qa.Xnulug/bin/notebooklm` before each generation; invocation log contains only fake operations | A01 |
| ADV-07 | C003 | Process/artifact residue | No related provider process or disposable fixture/artifact remains after cleanup | PASS; 0 exact `notebooklm` processes and 0 temporary QA roots | A02 |
| ADV-08 | C002/C003 | External authentication/live provider | Not applicable: the task explicitly prohibits authenticating or invoking real NotebookLM | NOT_APPLICABLE | A01 |

### artifactRefs

| ID | Kind | Description | Path |
|---|---|---|---|
| A01 | transcript | Fresh exact CLI invocations, fake-provider preflight, observed exit codes, parsed CSV/state assertions, provider operation log, and pre-cleanup inspection | `.omo/evidence/ulw/01a05f5f-031b-748b-a611-c92954a4249a/a4/manual-qa-fresh-transcript.txt` |
| A02 | receipt | Post-cleanup temporary-root and exact-provider-process checks | `.omo/evidence/ulw/01a05f5f-031b-748b-a611-c92954a4249a/a4/manual-qa-fresh-cleanup.txt` |
