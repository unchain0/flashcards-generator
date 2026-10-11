# Generate flashcards

The person picks PDF or PPTX files and asks for Anki cloze cards. The browser sends the files to the Companion. The hosted server does not receive them.

## Sub-features

- Before NotebookLM is connected, `#generation-fields` is disabled, **Gerar flashcards** is disabled, and `#job-empty` says `Nenhuma geração iniciada.`
- After connection, choosing files updates `#file-summary` with their names and enables **Gerar flashcards**.
- **Perfil de geração** `Geração geral` keeps `#language` editable (default `pt_BR`). `Inglês` disables `#language` and sends the English-study instructions with the form.
- A finished job sets `#job-status` `data-status` to `completed`, `#job-message` to `Geração concluída.`, and `#app-status` to `Geração concluída.` The counts line is `N de N arquivo(s) processado(s).`
- A job that finishes with failed sources sets `data-status` to `failed` and the message `A geração terminou com N arquivo(s) com falha.`

## How to get to it (user POV)

Sign in, click **Conectar NotebookLM**, and wait until the status is `NotebookLM conectado.` The form **Nova geração** then accepts **Arquivos PDF ou PPTX**, **Idioma dos cartões**, **Dificuldade**, **Quantidade**, **Limite por arquivo (segundos)**, **Perfil de geração**, and **Instruções adicionais (opcional)**. Submit with **Gerar flashcards**. Progress appears under **Status da geração**.

## Driving it with Playwright

Requires the connected end state from [connect-notebooklm.md](connect-notebooklm.md).

1. `page.getByLabel("Arquivos PDF ou PPTX").setInputFiles(pathToPdfOrPptx)`.
2. Wait until `#file-summary` shows the file name and **Gerar flashcards** is enabled.
3. Optionally set `#difficulty`, `#quantity`, `#timeout`, `#study-profile`, and `#instructions` by their labels: **Dificuldade**, **Quantidade**, **Limite por arquivo (segundos)**, **Perfil de geração**, **Instruções adicionais (opcional)**.
4. Click `getByRole("button", { name: "Gerar flashcards" })`.
5. Wait until `#job-status` has `data-status="completed"` or `data-status="failed"`. Polling is about every 1.2 seconds. Use a timeout long enough for the real NotebookLM job (the per-file limit defaults to 900 seconds).

Watch the network. `POST http://127.0.0.1:8766/v1/jobs` carries the multipart file. No request to the hosted origin should contain the file bytes. There is no hosted `/api/v1/jobs` route.

The observable end state is `#job-status`, the message text, and a **Baixar …csv** button when the Companion produced a CSV. Screenshot the status panel, and keep the response status of `POST /v1/jobs` (202 when accepted).

## Gotchas

- `setInputFiles` on a disabled `#files` does not count as a user choice. Connect NotebookLM first. Playwright's `isDisabled()` is false on the `#generation-fields` fieldset; use the file input and **Gerar flashcards**.
- Choosing `Inglês` sets the submitted language to `en` and `single_cloze` to true inside the page script. The visible `#language` field is then disabled. Assert that disabled state, and assert the Companion received `language=en`, rather than trusting the profile label alone.
- Generation without a real Google session fails before a job is accepted (`Conecte sua conta do NotebookLM antes de gerar flashcards.` on the Companion, HTTP 409). Do not mock that response and treat the mock as a pass.
- The Companion deletes its temporary upload workspace after the job. The proof that files were processed is the job status plus the downloaded CSV, not a leftover upload on the hosted server.
