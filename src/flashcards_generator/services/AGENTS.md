# SERVICES GUIDANCE

Root and package instructions apply. Services own application behavior and all
replaceable external contracts.

## LIVE MAP

- `use_cases.py`: public generation use case with explicit collaborators.
- `generation_*.py`: bounded run, document, chunk, retry, quality, security,
  artifact, and workflow responsibilities.
- `chunk_resume.py`: resumable state coordination.
- `dto/`: validated generation, merge, workflow, and local-job requests.
- `ports/`: NotebookLM, chunk state, document source, PDF chunker, snapshots,
  cancellation, deck, and Anki contracts.
- `workflows.py`, `generation_workflow.py`: public workflow facades.
- `local_generation_jobs.py`, `local_generation_state.py`: companion job
  orchestration and synchronized in-memory state.
- `web_authentication.py`: hosted authentication orchestration over a storage
  protocol.

## DEPENDENCIES

- Services may import `domain_models`, `engines`, standard library, and Pydantic.
- Services must not import `integrations` or `delivery`.
- External I/O is expressed through protocols/ABCs in `services/ports` or the
  narrow authentication storage protocol.
- Concrete implementations are passed from `delivery/composition.py` or the
  Litestar composition root.

## GENERATION RULES

- Preserve source-boundary checks, size/page limits, explicit timeouts, retry
  classification, resume signatures, atomic exports, and cleanup in `finally`.
- PDFs over the configured threshold may be chunked; PPTX conversion remains an
  integration concern.
- Successful combined decks are deduplicated and quality-filtered before CSV
  export.
- A failed document may be recorded without hiding unexpected programming
  errors; catch only classified operational failures.
- Keep output ordering and UTF-8 quoted two-column CSV behavior stable.

## LOCAL JOB RULES

- Job state is process-local and belongs to the user's companion, never the
  hosted server.
- Uploaded files live in a private temporary workspace and are removed after
  completion/cleanup.
- Keep ownership keyed by the verified opaque web subject.
- Bound worker count, upload count, file size, polling lifetime, and shutdown.

## CHANGE DISCIPLINE

- Do not default-construct new concrete integrations.
- Do not broaden ports for one implementation-specific convenience.
- Preserve causal chains when translating failures.
- Update focused normal/chunk/resume/retry/partial-failure/cleanup tests for
  behavioral changes.
