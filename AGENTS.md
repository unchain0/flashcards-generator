# PROJECT KNOWLEDGE BASE

**Updated:** 2026-09-28
**Architecture:** MASA

## OVERVIEW

Python 3.14 Litestar application with a Vite+ browser frontend. The hosted web
application owns password-only access and issues short-lived capabilities to a
local `flashcards-companion`. The companion owns each user's Google/NotebookLM
profile, receives PDFs/PPTX directly from the browser, generates cards, and
returns CSV artifacts without sending source documents to the hosted server.

## STRUCTURE

```text
.
├── frontend/                       # Vite+ TypeScript browser application
├── migrations/                     # Alembic schema migrations
├── scripts/python/                 # Quality-gate implementation
├── src/flashcards_generator/
│   ├── domain_models/              # Entities, value objects, exceptions
│   ├── engines/                    # Pure deterministic transformations
│   ├── services/                   # Use cases, DTOs, ports, orchestration
│   ├── integrations/               # DB, filesystem, PDF, NotebookLM, Anki
│   └── delivery/                   # Litestar apps and concrete composition
├── tests/                          # Unit, integration, and support fixtures
├── Dockerfile
└── docker-compose.yml
```

## WHERE TO LOOK

| Task | Location |
| --- | --- |
| Hosted web routes and security headers | `src/flashcards_generator/delivery/web/` |
| Local companion routes and uploads | `src/flashcards_generator/delivery/companion/` |
| Concrete dependency wiring | `src/flashcards_generator/delivery/composition.py` |
| Generation orchestration | `src/flashcards_generator/services/use_cases.py`, `generation_*.py` |
| Local job lifecycle | `src/flashcards_generator/services/local_generation_*.py` |
| Request validation | `src/flashcards_generator/services/dto/` |
| External contracts | `src/flashcards_generator/services/ports/` |
| Card/deck/resume schemas | `src/flashcards_generator/domain_models/` |
| Cloze, math, and quality rules | `src/flashcards_generator/engines/` |
| NotebookLM integration | `src/flashcards_generator/integrations/notebooklm/` |
| PDF/PPTX and semantic processing | `src/flashcards_generator/integrations/` |
| Password/session persistence | `src/flashcards_generator/integrations/web_*.py` |
| Browser controller/view/API | `frontend/src/` |

## MASA DEPENDENCY RULES

- `domain_models` and `engines` are inward-facing and do not import outer
  layers.
- `services` owns use cases and ports. It may import `domain_models` and
  `engines`, but never `integrations` or `delivery`.
- `integrations` implements service ports and technical boundaries. It must not
  import `delivery`.
- `delivery` validates transport input, translates errors, and composes concrete
  integrations with services.
- The TypeScript frontend follows the same direction through
  `domain -> application -> infrastructure/interfaces`, composed in `main.ts`.

## SECURITY AND DATA RULES

- The hosted database stores only opaque user IDs, Argon2id password hashes,
  keyed password lookups, and hashed session tokens. Do not add names, emails,
  source documents, generated cards, or Google credentials.
- Source files and NotebookLM browser profiles remain on the user's computer.
- Production requires PostgreSQL, Alembic migrations, HTTPS, explicit secrets
  of at least 32 characters, and `FLASHCARDS_AUTO_CREATE_SCHEMA=false`.
- Keep session cookies `HttpOnly`, `Secure` in production, and `SameSite=Lax`.
- Every subprocess needs an explicit timeout and bounded output/cleanup.

## CONVENTIONS

- Python is pinned to 3.14.7. Use `uv run --frozen ...` for reproducible checks.
- The frontend pins `pnpm@12.5.1`; use Corepack/pnpm, never npm.
- Ruff owns Python lint/format, `ty` owns production type checking, and Radon
  must report rank A for every production function and module.
- Frontend TypeScript is strict and Vitest coverage thresholds remain 100% for
  statements, branches, functions, and lines.
- Use Pydantic v2 models at validated boundaries and preserve exception causes
  with `raise ... from error`.
- Log through `integrations.logging_config.get_logger`; do not use `print()` in
  application/runtime code.

## ANTI-PATTERNS

- Do not resurrect the removed CLI/TUI or the old `application`, `domain`,
  `infrastructure`, `adapters`, or `interfaces` packages.
- Do not instantiate concrete integrations inside `services`; wire them in
  `delivery/composition.py` or a Litestar composition root.
- Do not proxy source uploads through the hosted web server.
- Do not store Google/NotebookLM authentication on the server.
- Do not enable metadata `create_all()` in production; migrations own schema
  changes.
- Do not add blanket ignores, untyped public definitions, `Any` escapes, or
  hardcoded machine paths.
- Do not describe mocked integration tests as live NotebookLM coverage.

## COMMANDS

```bash
corepack pnpm@12.5.1 --dir frontend install --frozen-lockfile
corepack pnpm@12.5.1 --dir frontend run check
corepack pnpm@12.5.1 --dir frontend run test
corepack pnpm@12.5.1 --dir frontend run build
corepack pnpm@12.5.1 --dir frontend run e2e
uv sync --all-extras --dev
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen ty check src/flashcards_generator
uv run --frozen pytest
uv run --frozen task quality-gate
uv build
uv run --frozen pre-commit run --all-files --show-diff-on-failure
docker compose build
```

## NOTES

- Build the Vite+ assets before starting or testing the hosted app; Litestar
  serves `delivery/web/static/dist` and refuses to start without it.
- `flashcards-web`, `python -m flashcards_generator`, and `main.py` start the
  hosted server. `flashcards-companion` is the only end-user local process.
- `delivery/web/user_admin.py` is an operational password-provisioning command,
  not a replacement for the removed generation CLI.
- Preserve unrelated working-tree changes and never reset this migration.
