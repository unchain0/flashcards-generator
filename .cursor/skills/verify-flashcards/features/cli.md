# CLI workflows

The installed Rust `flashcards` command is the local non-browser surface. It
uses the same document preparation, NotebookLM, CSV export, and merge crates as
the Companion. It does not send documents or credentials to the hosted server.

## User-facing commands

- `flashcards --json <command> ...` emits one JSON result on stdout; without it,
  the commands print a short human-readable summary.
- `flashcards --help` lists `generate`, `merge`, and `notebook`.
- `flashcards merge --folder <dir> [--output <name>] [--deduplicate] [--no-recursive]`
  combines local CSV files and never imports them into Anki.
- `flashcards notebook status --storage-file <file>` and
  `flashcards notebook list --storage-file <file>` read a local NotebookLM
  storage-state file without changing notebooks.
- `flashcards generate --input-dir <dir> [--output-dir <dir>]` creates CSVs from
  local PDF/PPTX files.
  `--language`, `--difficulty`, `--quantity`, `--timeout`, `--instructions`,
  `--single-cloze`, `--include`, `--exclude`, and `--files` refine the request.
- `flashcards notebook cleanup --all` deletes every notebook visible to the
  supplied local credentials and is destructive; do not drive it in routine QA.

## Sanity check

```bash
cargo run --locked -p flashcards-cli -- --help
cargo run --locked -p flashcards-cli -- merge --help
cargo run --locked -p flashcards-cli -- notebook --help
```

For NotebookLM commands, pass `--storage-file <absolute-private-file>` or set
`FLASHCARDS_NOTEBOOKLM_STORAGE_FILE`. Never use a real storage-state file in
automated evidence, and never treat a mocked browser test as native CLI proof.
