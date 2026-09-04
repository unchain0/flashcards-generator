# Code quality review - G001

## Verdict

- **codeQualityStatus:** WATCH
- **recommendation:** APPROVE
- **blockers:** None.

This was an independent read-only review of the complete unstaged diff and every changed production path. No CRITICAL or HIGH regression was found.

## Scope and verification

- Inspected the current diff (43 modified tracked files plus `tests/unit/test_contracts.py`) and the current contents of all changed production modules.
- Read the root and package/infrastructure architecture guidance, the ULW goal/brief, and the prior evidence as untrusted context. The prior evidence is stale in one material respect: it says the strict complexity gate fails, while the current independent `uv run task quality-gate` exits 0.
- Independently ran: `uv run ruff check .`, `uv run ruff format --check .`, `git diff --check`, `uv run mypy src/flashcards_generator`, `COVERAGE_FILE=$(mktemp) uv run pytest`, and `uv run task quality-gate`.
- Results: lint, formatting, diff check, mypy, and quality gate passed; pytest passed **427/427** in 62.71s. LSP diagnostics found zero errors in 30 production and 38 test files.

## Skill-perspective check

The required `remove-ai-slops` and `programming` skill check was **unavailable**: no loadable skill artifacts were present in the workspace or user skill locations exposed to this reviewer. I applied the requested criteria directly.

- **remove-ai-slops:** production behavior-oriented regressions are generally tested; no deletion-only, prose-only, or implementation-constant-only test was found among the substantive additions. The tiny single-use assertion helpers below are avoidable test slop.
- **programming:** the diff has no untyped production definitions, type-ignore escape hatches, brittle prompt/prose assertions, or unneeded production parsing at the CLI/request boundary. It does introduce a duplicated active NotebookLM workflow, contrary to the documented architecture boundary.

## Findings

### CRITICAL

None.

### HIGH

None.

### MEDIUM

1. **Current diff grows a second, unused NotebookLM command workflow instead of keeping the adapter as the sole active owner.**
   - `src/flashcards_generator/infrastructure/notebooklm_client.py:27-267`
   - Duplicate provider-response parsing/card conversion: `src/flashcards_generator/infrastructure/notebooklm_client.py:50-86,202-256` and `src/flashcards_generator/adapters/notebooklm_adapter.py:347-434`.
   - The client now independently implements create/add/wait/generate/download/delete, command dialect, deadlines, response parsing, and error behavior, while it has no production reference outside tests. This conflicts with the local boundary that the adapter owns the active `FlashcardGeneratorPort` workflow and that `NotebookLMClient` must not become a competing workflow. It creates two locations that must stay in lockstep when the provider contract changes. This is an introduced maintainability/architecture regression, not a demonstrated current correctness failure.

2. **The complexity refactor adds four single-use test assertion wrappers, increasing indirection without reuse or new behavioral coverage.**
   - `tests/unit/test_use_cases_resume.py:88-126`
   - Each wrapper is called once and hides adjacent, scenario-specific assertions. This is low-value test abstraction under the requested remove-ai-slops/programming perspectives. It is not a correctness failure and does not invalidate the tests.

### LOW

None.

## Residual debt distinguished from this diff

- The global Loguru sink replacement in `src/flashcards_generator/infrastructure/logging_config.py:20` remains an embedding-host observability risk, but it is pre-existing behavior rather than a regression introduced here.
- Real authenticated NotebookLM/provider behavior remains intentionally unverified; the current adapter/client contract coverage uses mocks and a fake CLI. This is an external integration residual, not a defect established by this review.

## Approval rationale

The major changed paths preserve their public and persisted contracts under the independently passing full suite: descriptor-backed source snapshots, resume-state validation/locking, atomic state persistence, CSV/TSV quoting, subprocess timeout cleanup, response validation, and PDF reader cleanup all have relevant behavior tests. The two MEDIUM maintainability findings should be scheduled, but neither creates a concrete release blocker for the stated goal.
