# C005 - final post-fix independent audit

Date: 2026-09-02
Working directory: `/home/avell/Projects/unchain0/flashcards-generator`

The first independent audit exposed two security blockers and the first
hands-on QA exposed two data-integrity/status blockers. They were reproduced
with failing tests and corrected:

1. LibreOffice conversion now runs in a new process group and terminates and
   reaps that group on timeout or cancellation.
2. PDF file/page/page-text limits, JSON byte/card-count limits, and sparse
   semantic boundary computation prevent the previously unbounded paths.
3. CSV merge writes a private sibling temporary file, fsyncs it, and replaces
   the destination only after all input rows validate.
4. Resume locks are acquired only for chunked resumable PDFs, so regular
   failures leave no lock artifact.
5. The use case exposes processing failures and the CLI returns exit 1
   instead of reporting a failed generation as success.

Final real-surface QA, independently repeated by the QA executor using a
command-resolved disposable fake provider:

- `python -m flashcards_generator --help`: exit 0.
- `flashcards --help`: exit 0.
- Fake local generation with a one-page PDF: exit 0; one valid two-column
  Cloze CSV row; no lock or source snapshot remained.
- Malformed merge: exit 1; prior merged output remained byte-for-byte intact.
- Fake provider source failure: exit 1; no CSV, no traceback, and no lock.
- Final process probe found no target process; generated QA/build artifacts
  were removed.

Fresh QA artifacts:

- `.omo/evidence/ulw/01a05f5f-031b-748b-a611-c92954a4249a/a4/G001-aprimore-e-audite-integralmente-o-pr-manual-qa.md`
- `.omo/evidence/ulw/01a05f5f-031b-748b-a611-c92954a4249a/a4/manual-qa-fresh-transcript.txt`
- `.omo/evidence/ulw/01a05f5f-031b-748b-a611-c92954a4249a/a4/manual-qa-fresh-cleanup.txt`

Final independent review:

- Security/concurrency reviewer: PASS, high confidence, no blockers
  (`st_01a06040-code-review.md`).
- Quality reviewer: APPROVE, no blockers.
- Gate review findings about stale 426/427 evidence and missing C004/C005
  artifacts were addressed by the canonical evidence files in this
  directory; current official C001-C003 metadata now points to those files
  and records the final 437-test/31-source-file results.

Residual risks, explicitly accepted and not hidden:

- Live NotebookLM authentication/provider compatibility was not exercised.
- Sparse similarity can still be quadratic in time on dense adversarial text,
  although retained pairs are capped at 100,000 and no dense all-pairs matrix
  is allocated.
- Global Loguru sink replacement can affect embedding hosts.

Result: PASS. No critical or high blocker remains in the current tree.
