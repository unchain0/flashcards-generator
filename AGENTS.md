# Repository guide

Rust workspace that turns local PDF and PPTX files into Anki cloze flashcards through NotebookLM. The product is the `flashcards` command-line binary; there is no server, web interface, database, or hosted deployment.

## Structure

```text
rust/
├── cli/                  # flashcards binary: auth, generate, merge, notebook
├── domain/               # entities and value objects
├── engines/              # pure cloze, math, and quality transformations
├── services/             # use cases, DTOs, and ports
├── integrations/         # NotebookLM client, browser login, PDF/PPTX, Anki, CSV
└── integrations-shared/  # logging, monitoring, bounded HTTP helpers
```

Dependencies point inward: `cli -> integrations -> services -> engines -> domain`. `integrations-shared` holds technical helpers used by `integrations`.

## Commands

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked
cargo test --workspace --locked
cargo build --release --locked
cargo run -p flashcards-cli -- --help
```

Interactive login needs a browser, so it is not covered by tests. Everything else is exercised with local fixtures, `httptest`-style servers, and temporary directories.

## Where to look

| Task | Location |
| --- | --- |
| Command surface and exit codes | `rust/cli/src/main.rs` |
| Saved-session handling | `rust/integrations/src/notebooklm_profiles.rs` |
| Browser login and session capture | `rust/integrations/src/notebooklm_browser.rs` |
| NotebookLM HTTP and RPC | `rust/integrations/src/notebooklm.rs` |
| Credential parsing and cookie scoping | `rust/integrations/src/notebooklm/protocol.rs` |
| Generation orchestration | `rust/services/src/generation_*.rs` |
| CSV merge and export | `rust/integrations/src/csv_merge.rs`, `deck_exporter.rs` |
| PDF/PPTX preparation | `rust/integrations/src/pdf.rs`, `pptx.rs` |

## Rules

- Keep credentials off disk paths that other users can read: session directories are `0700`, session files `0600`, and links or non-regular files are rejected.
- Never log cookies, session tokens, account emails, or source contents.
- Every subprocess needs a timeout, bounded output, and cleanup. Browser and converter failures must not leave processes behind.
- Validate at boundaries: source paths, globs, storage files, and provider responses.
- Do not reintroduce a hosted service, database, or web client into this repository.

## Known gaps

- The NotebookLM integration follows private web endpoints; a service change can break upload, generation, or listing without a code change here.
- The session is not refreshed automatically; an expired session requires `flashcards auth login`.
- `auth login` requires a graphical session and an installed Chrome or Chromium.
