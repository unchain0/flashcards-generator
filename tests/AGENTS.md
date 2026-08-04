# TESTS KNOWLEDGE BASE

This file is subordinate to the repository-root `AGENTS.md`.
Root architecture, Python, typing, formatting, and safety rules still apply.

## LAYOUT

```text
tests/
├── conftest.py                 # Plugin registration; shared temp dirs
├── fixtures/                   # Python pytest fixture modules
├── integration/                # Cross-layer isolated boundary tests
├── unit/                       # Main module-level test suite
└── test_semantic_chunking.py   # Semantic chunker/filter coverage
```

- `fixtures/` contains Python modules, not checked-in sample PDFs or JSON.
- `conftest.py` registers all fixture modules through global
  `pytest_plugins`; fixtures are therefore available suite-wide.
- Domain fixtures provide cards, decks, configuration, and source metadata.
- Infrastructure fixtures provide mocked clients, subprocesses, converters,
  and exporters.
- Adapter fixtures provide `MockFlashcardGenerator` and sample card payloads.

## TEST BOUNDARIES

- Unit tests isolate subprocesses and NotebookLM through mocks, fakes, or port
  test doubles. Keep external calls deterministic and offline.
- Current integration tests do not call the live NotebookLM API.
- `test_client_integration.py` patches `subprocess.run` and checks parsing plus
  command-boundary behavior.
- `test_resume_flow.py` combines a fake generator with real temporary files,
  generated PDFs, CSV output, and filesystem-backed resume state.
- Use `tmp_path` for ordinary filesystem tests. Use `temp_dirs` only when the
  paired input/output directory shape is useful; it cleans up after yielding.
- Generate minimal PDFs with `pypdf.PdfWriter` inside temporary directories.
  Do not add binary fixture files for cases that can be created in-test.

## HOTSPOTS

- `unit/test_use_cases.py`: central generation, chunking, cleanup, and failure
  orchestration coverage.
- `unit/test_use_cases_resume.py`: focused resume manifest behavior.
- `integration/test_resume_flow.py`: end-to-end resume across persisted files.
- `unit/test_notebooklm_adapter.py` and `unit/test_notebooklm_client.py`:
  subprocess protocol, parsing, retries, timeouts, and cleanup.
- `unit/test_cli.py`, `unit/test_cli_cleanup.py`, `unit/test_cli_merge.py`:
  argparse wiring, exit paths, cleanup, and merge commands.
- `unit/test_pdf_utils.py` and `test_semantic_chunking.py`: PDF and semantic
  processing seams.

## CONVENTIONS

- Pytest discovers `test_*.py`, `Test*`, and `test_*` under `tests/`.
- The current discovery baseline is 361 tests (`uv run pytest --collect-only`);
  intentional additions or removals must explain collection-count changes.
- Pytest runs with `--strict-markers`; only `unit` and `integration` are
  registered in `pyproject.toml`. Register a new marker before using it.
- Prefer behavior assertions and externally visible state over private-call
  choreography. Assert cleanup and persisted state on failure paths.
- Patch where the dependency is looked up. Give subprocess doubles explicit
  stdout, stderr, return code, timeout, and exception behavior as relevant.
- Keep tests independent of authentication, network access, local NotebookLM
  state, wall-clock sleeps, and test execution order.
- Add regression tests beside the narrowest affected module. Use integration
  coverage only when multiple real local boundaries are material to the bug.

## TEST-SPECIFIC COMMANDS

```bash
uv run pytest --collect-only -q
uv run pytest tests/unit
uv run pytest tests/integration -m integration --timeout=300
uv run pytest tests/unit/test_use_cases.py -k <behavior>
```

- Run the narrowest affected test first, then the full suite before handoff.
- Integration means isolated cross-layer coverage here; it is safe from live
  API rate limits because NotebookLM remains mocked or faked.
