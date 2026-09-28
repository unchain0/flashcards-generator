# PACKAGE KNOWLEDGE BASE

Root `AGENTS.md` owns project-wide policy. This file maps the installable
package and its MASA boundaries.

## LIVE MAP

```text
flashcards_generator/
├── domain_models/  # Pydantic entities, value objects, domain exceptions
├── engines/        # Pure cloze, math, and quality transformations
├── services/       # DTOs, ports, use cases, workflows, local jobs
├── integrations/   # NotebookLM, Anki, PDF/PPTX, filesystem, SQLAlchemy
├── delivery/       # Web/companion Litestar apps and composition
├── __main__.py     # hosted web entrypoint
└── __init__.py
```

## OWNERSHIP

- `domain_models`: stable business data and failures only.
- `engines`: deterministic computation with no filesystem, network, process, or
  database access.
- `services`: orchestration and ports; never imports `integrations` or
  `delivery`.
- `integrations`: concrete I/O and implementations of service ports.
- `delivery`: transport validation, HTTP translation, process lifecycle, and
  concrete dependency composition.

## ENTRYPOINTS

- `flashcards-web` / `python -m flashcards_generator`: hosted Litestar app.
- `flashcards-companion`: loopback-only local NotebookLM and generation app.
- `python -m flashcards_generator.delivery.web.user_admin create`: operational
  password provisioning after migrations.

## CHANGE RULES

- Keep generation behavior reachable through service ports and workflows.
- Keep HTTP concepts in `delivery`; keep SQLAlchemy, pypdf, subprocesses, and
  filesystem implementations in `integrations`.
- Add pure transformations to `engines` only when they have no I/O.
- Coordinate persisted resume-model changes across `domain_models`, service
  ports, the filesystem repository, and tests.
- Preserve the local-companion boundary: hosted routes never receive source
  documents or Google credentials.
- Do not recreate removed legacy packages or end-user CLI/TUI surfaces.
