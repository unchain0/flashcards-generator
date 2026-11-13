# Flashcards Generator local CLI migration

## Goal

Make the supported product a local command-line app. A user signs into NotebookLM through its browser login flow, then runs flashcard generation headlessly from the local CLI. The project will not require its own web UI, companion server, database, or VPS deployment.

## Approach

Keep the existing Rust `flashcards` CLI and generation pipeline. Add a thin `auth login` command that invokes the installed `notebooklm` CLI to open the browser login and save its local session. Reuse that session for headless generation. Remove the separate hosted web and Companion product surfaces and their deployment configuration after the CLI workflow verifies locally. This keeps the tested Rust generation path and avoids rebuilding the old Python TUI or duplicating NotebookLM protocol code.

## Data shape

A local generation request is `{input_dir, output_dir, difficulty, quantity, language, instructions, selected_files, notebooklm_storage_file}`. The storage file is the upstream NotebookLM CLI's local `storage_state.json`; it stays on the user's computer with owner-only permissions.

## Scope

Included: browser-based NotebookLM sign-in on the user's computer, headless generation, local CSV merge, local notebook management, concise CLI documentation, tests, and removal of the app's web/server/VPS paths.

Excluded: running the browser or authenticating a Google account during this code change, changing or deleting live production data, stopping or deleting the currently deployed service, and activating master-token authentication.

## Alternatives

- Restore the historical Python Textual application. Reuses its prior interface but would replace the current Rust generator and reintroduce a second implementation.
- Keep the web UI and Companion and use the CLI as an optional local path. This leaves the hosting and browser workflow the user asked to remove.
- Keep the Rust CLI as the sole product and call NotebookLM's supported local login command. Chosen because the CLI already owns generation, merge, and notebook operations.

## Phases

1. Add and test `flashcards auth login`; share one default NotebookLM profile path between login and generation.
2. Make local CLI the build, installation, and test path; remove service/Compose deployment entry points.
3. Remove hosted web UI and Companion code, dependencies, tests, and obsolete setup documentation while retaining the Rust CLI core.
4. Run complete local CLI quality gates and verify the packaged executable and login handoff without accessing a real Google account.

## Verification

Use the Rust workspace's targeted tests, format, Clippy, and CLI build. Exercise `flashcards --help`, `flashcards auth login --help`, and `flashcards generate --help`. Test login command construction with a fake `notebooklm` executable. Confirm web/VPS deployment entrypoints are absent and the CLI accepts the upstream storage-state format. Do not deploy or alter the running service.

## Risks

NotebookLM's API is unofficial and may change independently. The browser login command and generation client must agree on the storage-state path and format. Removing server-side sources does not stop an already deployed service; infrastructure retirement remains separate.