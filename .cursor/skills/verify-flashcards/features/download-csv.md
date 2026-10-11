# Download CSV

The person saves the generated cloze deck. The file is a two-column CSV downloaded from the Companion, not from the hosted site.

## Sub-features

- A completed job with artifacts shows one button per file, labeled `Baixar <name>`.
- A completed job with no CSV shows the list text `A geração terminou sem CSV novo.` and no download button.
- Clicking a download button starts a browser download and sets `#app-status` to `Download iniciado.`

## How to get to it (user POV)

Finish a generation from [generate-flashcards.md](generate-flashcards.md). Under **Status da geração**, the file list `#job-files` has the **Baixar** buttons. The empty state **Nenhuma geração iniciada.** is hidden once a job exists.

## Driving it with Playwright

From a job whose `#job-status` `data-status` is `completed` and that rendered a download button:

1. `const downloadPromise = page.waitForEvent("download")`.
2. `page.getByRole("button", { name: /^Baixar .+\.csv$/ }).click()`.
3. `const download = await downloadPromise`.
4. Read `download.path()` and the suggested filename.

The file must be non-empty UTF-8 CSV and contain a cloze marker `{{c1::`. The button's `data-artifact-url` points at the Companion, shaped like `/v1/jobs/<id>/artifacts/<name>`. The request host is `127.0.0.1:8766`.

Save a copy of the CSV text into `.cursor/skills/verify-flashcards/evidence/` (for example `downloaded.csv`) plus a screenshot of **Download iniciado.** The repo gitignores `*.csv` at the root; this evidence path is under the skill directory and is gitignored by `evidence/.gitignore`, which is what we want for a runtime download.

## Gotchas

- `saveArtifact` in the page clicks a temporary object URL. Playwright still emits a `download` event for that click. If `download.path()` is null, the proof failed.
- Do not treat a CSV written by a unit test, or by `frontend/e2e` against the fake Companion, as this feature. The bytes have to come from the download the button triggered.
- The hosted database must not gain a copy of the card text. It only has `web_users` and `web_sessions`.
