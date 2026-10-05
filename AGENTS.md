# PROJECT KNOWLEDGE BASE

**Updated:** 2026-10-05
**Architecture:** MASA

## RUST MIGRATION

The user requested a complete Rust rewrite on 2026-10-02. Rust replaces the
Python hosted backend and local companion; Vite+ and pnpm remain. The Python
implementation below remains the behavioral reference. On 2026-10-05 the user
explicitly authorized publishing this Rust version after being informed that
the strict coverage gate still fails. The production Compose selects
`Dockerfile.rust`; this release exception does not lower coverage thresholds or
establish complete native Companion acceptance.

The Cargo workspace lives in `rust/`. `domain` owns pure entities, `engines`
owns deterministic transformations, `services` will own use cases and ports,
`integrations` owns external implementations, and `delivery` will compose the
hosted server and companion. Dependencies must point inward. Keep all source
documents and Google profiles local, and preserve existing database identities,
password hashes, session hashes, and browser API contracts during migration.

Rust integrations and delivery also separate data locations. `integrations`
and `delivery` contain local Companion capabilities. `integrations-server` and
`delivery-server` contain hosted authentication, persistence, and web transport.
Their `*-shared` counterparts contain bounded HTTP helpers, monitoring, request
authorization, and server lifecycle code. Hosted and shared crates cannot depend
on local crates, including through test or build dependencies. The xtask checks
both MASA direction and this location boundary; it does not prove I/O purity.

After building frontend assets, run `bash scripts/rust-check.sh` to test the
Rust workspace against disposable PostgreSQL and exercise the hosted binary,
including migration, provisioning, HTTP authentication, assets, and restart.
It also checks the Companion on port 8766 and runs browser authentication and
UI contracts against Rust with a simulated Companion. This does not prove live
NotebookLM generation or native Companion browser generation.
`Dockerfile.rust` builds the hosted Rust executable and frontend assets without
shipping Python, Cargo, source documents, or local Google profiles. Build with
`docker build -f Dockerfile.rust -t flashcards-generator:rust-qa .`, then run
`bash scripts/rust-container-check.sh`. The check uses isolated disposable
PostgreSQL, a read-only application filesystem, and restricted container
permissions. It covers startup migrations, assets, cookies, persisted sessions
after graceful restart, and logout revocation. The default Dockerfile remains
the Python reference; production Compose selects `Dockerfile.rust` under the
publication authorization above.
The Rust AnkiConnect client uses local port 8765. Run
`cargo run --locked -p flashcards-delivery --example anki_smoke` for a read-only
version/deck-count check; it never imports notes. `FLASHCARDS_ANKI_API_KEY` is
optional when the local add-on requires an API key.
Rust CSV merging preserves source order and two-column contents, with optional
deduplication of trimmed front/back pairs. It excludes its output, rejects
symlink sources, and publishes private files atomically. Limits are 512 MiB of
aggregate input, 100,000 directory entries, and 1,000,000 valid rows.
`cargo run --locked -p flashcards-delivery --example notebooklm_smoke -- <storage-file>`
checks native NotebookLM authentication and lists only a notebook count. It
does not upload documents or change notebooks. A successful Python passive auth
check does not prove the Rust client handles Google's authentication redirects.
`cargo run --locked -p flashcards-delivery --example notebooklm_browser_smoke -- <storage-file> <browser-profile> [session-output] [browser-profile-output]`
checks native browser login using a bounded private copy of a dedicated Google
profile, verifies captured credentials, compares notebook IDs when the initial
session is valid, and confirms that the original browser profile remains
unchanged. Without an authenticated baseline, inventory preservation is not
demonstrated. It rejects active Linux profiles,
uploads no documents, and may require interactive login if the copied session
has expired. Reusing a session does not prove fresh Google credential entry.
The optional session output is a new file in an existing private absolute
directory. Only verified credentials are exported, atomically without replacing
an existing file, for subsequent local QA. The original profile remains unchanged.
The last optional argument retains the authenticated profile in a separate,
empty private absolute directory. Profile copying is bounded, rejects active
profiles and unsupported entries, and rejects destinations inside the source.
The same QA example supports `--copy-profile <source> <empty-private-destination>`
without accessing Google. This is development tooling, not a generation CLI.
Native authentication and browser login use `https://notebook.google.com`.
Bootstrap redirects are restricted to the two personal NotebookLM hosts and
`accounts.google.com`, with scoped cookies, six requests, and a total 30-second
timeout. RPC and upload requests do not follow redirects. RPC frame lengths
are advisory; JSON parsing and unique matching method envelopes remain required.
Account routing reads local `notebooklm.account` metadata, preferring its email
over the account index, and also accepts the Companion's legacy `authuser` field.
This metadata remains local; routing headers are sensitive and HTTP errors omit
request URLs. `cargo run --locked -p flashcards-delivery --example notebooklm_generation_smoke -- <storage-file>`
creates a temporary QA notebook from built-in synthetic cell-biology text,
generates and downloads cards, then confirms cleanup and preservation of the
original notebooks. It never uploads personal documents or imports Anki notes;
it does not prove document upload or native Companion browser generation.
Interactive cards accept both plain string fields and Google's
`flashcardContentBlock` arrays, preserving text/Markdown block order and math.
Unsupported media, malformed text, and more than 128 blocks per side fail
explicitly rather than silently dropping content.
`cargo run --locked -p flashcards-delivery --example notebooklm_companion_smoke -- <storage-file>`
runs an opt-in live browser check with the hosted Rust server, native Companion,
disposable PostgreSQL, and a private temporary copy of the local Google session.
It creates a synthetic cell-biology PDF using Playwright Chromium, uploads only
to the Companion, waits for real Google generation, downloads the CSV, and
checks notebook cleanup and preservation. Screenshots remain in
`frontend/test-results/live-companion/`. It requires qpdf and pdftotext, never
imports Anki notes, and does not prove fresh interactive Google login. Regular
browser tests do not discover this opt-in `.live.ts` test.
Pass `pdf <browser-profile>` after the storage file to include native login
through the Companion route using a bounded private copy of a closed dedicated
profile, then real generation. This reuses a session and does not prove fresh
Google credential entry. Failed job submissions fail the browser check directly.
Pass `pptx` after the storage file to exercise the existing synthetic presentation
fixture, native LibreOffice conversion, and real Google generation. Both modes
use the optimized release Companion, verify a completed browser job, and download
a non-empty CSV with cloze content.
The native Companion is installable with
`cargo install --locked --path rust/delivery --bin flashcards-companion`.
Runtime dependencies are qpdf, Chrome/Chromium, and LibreOffice for PPTX;
Python is not a runtime dependency. Configure the exact hosted origin in
`FLASHCARDS_COMPANION_WEB_ORIGIN` and keep profiles on the user's computer.
File registration accepts duplicate source rows only when their filename matches
exactly and all matching rows identify the same new source. Ambiguous IDs fail.
Direct `cargo test --workspace --locked` requires a disposable database URL in
`FLASHCARDS_TEST_DATABASE_URL`. Rust is pinned in `rust-toolchain.toml`.
Existing Python coverage does not prove Rust
coverage. Consult ChatGPT in the browser before selecting replacement quality
verifiers; Radon applies only to the Python reference.
`bash scripts/rust-coverage.sh` optionally accepts one local Google storage-state
file and a second closed dedicated browser-profile directory to include real
synthetic PDF/PPTX generation with the instrumented native
Companion. This opt-in mode verifies notebook preservation and QA cleanup,
uses existing instrumented binaries, and keeps the session on the user's computer.

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

- Python is pinned to 3.14.8. Use `uv run --frozen ...` for reproducible checks.
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
