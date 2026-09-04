# Cleanup receipt

Date: 2026-09-02
Working directory: /home/avell/Projects/unchain0/flashcards-generator

- Temporary fake NotebookLM executables, valid-PDF fixtures, CSV fixtures,
  output trees, and malicious-path probes were created below disposable
  temporary directories and removed by shell traps.
- Build artifacts were inspected before cleanup, then dist/, build/,
  src/flashcards_generator.egg-info/, .pytest_cache/, .mypy_cache/,
  .ruff_cache/, htmlcov/, and coverage.xml were removed.
- The tracked coverage database was restored exactly to HEAD:
  SHA-1 object 6a218c7dcdb1b0cee25ac34c553d0126d1093082 and mode 0755.
- No NotebookLM process remains: the final process probe found no matching
  process beyond the probe command itself.
- No git commit was created. No reset, checkout, deployment, authentication,
  or user-data mutation was performed.
- The .omo/ulw-loop state and .omo/evidence/ulw reports are intentionally
  retained as the requested audit evidence and are not disposable QA output.

Verification: generated build/test artifacts are absent; only scoped project
changes, requested loop state, and evidence reports remain.
