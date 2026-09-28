# INTEGRATIONS GUIDANCE

Concrete technical boundaries. Root and package instructions still apply.

## LIVE MAP

- `notebooklm/`: executable discovery, process runner, response parser, catalog,
  management, lower-level client, and the generator gateway.
- `anki/connect.py`: AnkiConnect implementation.
- `pdf_utils.py`, `fixed_size_pdf_chunker.py`, `pptx_converter.py`,
  `semantic_chunker.py`, `semantic_analysis.py`: bounded document processing.
- `chunk_state_repository.py`: atomic resumable manifests and chunk results.
- `document_sources.py`, `source_snapshot.py`: filesystem implementations of
  service ports.
- `web_models.py`, `web_database.py`, `web_auth.py`: SQLAlchemy persistence,
  Argon2id password handling, sessions, and companion capabilities.
- `logging_config.py`, `desktop_actions.py`, `document_limits.py`: shared
  technical helpers.

## DEPENDENCY RULES

- Integrations may depend on `services` ports/contracts, `domain_models`, and
  `engines` where needed.
- Never import `delivery`.
- Do not place application orchestration here; implement the narrow contract
  owned by services.
- Deterministic cloze, math, and quality behavior belongs in `engines`.

## I/O AND PROCESS RULES

- Every subprocess has an explicit timeout, captured bounded output, and
  termination/cleanup appropriate to the API.
- Pass `Path` objects through filesystem code and reject traversal/symlink
  escapes at trust boundaries.
- Preserve atomic writes with temporary siblings plus `replace()`.
- Close pypdf streams on every path and enforce finite file, page, text, and
  provider-artifact limits.
- Log through `get_logger`; never log credentials, session tokens, source
  contents, or unbounded command output.

## AUTH AND DATABASE RULES

- Passwords use Argon2id; keyed lookups and session-token hashes use separate
  configured secrets.
- The database stores only opaque IDs, password material, and sessions.
- Production schema changes are Alembic migrations. `create_all()` is allowed
  only for development/test when explicitly configured.
- Companion capabilities are short-lived, signed, bound to a live session, and
  verified against the hosted origin.

## NOTEBOOKLM RULES

- The companion owns the local browser profile and provider process lifecycle.
- Keep provider command details inside `notebooklm/`; services see only ports.
- Translate required-operation failures to contextual domain exceptions and
  preserve causes.
- Do not store or run a user's Google session on the hosted server.
