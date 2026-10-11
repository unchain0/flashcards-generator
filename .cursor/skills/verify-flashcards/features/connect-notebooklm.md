# Connect NotebookLM

The person checks the Google NotebookLM session that lives in the Companion on this computer. The hosted server never receives the Google profile.

## Sub-features

- Companion not running. `#notebook-status` becomes `O Flashcards Companion não respondeu. Inicie o auxiliar e autorize o acesso local no navegador.` and its `data-state` is `error`. `#generation-fields` stays disabled.
- Companion up, no Google session. The status becomes `Nenhuma conta do NotebookLM conectada.` and `data-state` is `disconnected`. The Companion may open a browser window for Google login. Generation stays disabled.
- Companion up and already authenticated. The status becomes `NotebookLM conectado.` and `data-state` is `connected`. `#generation-legend` becomes `Escolha os arquivos para configurar a geração.` and `#generation-fields` is enabled.

## How to get to it (user POV)

Sign in. The panel **Conexão com o NotebookLM** is on the dashboard, with the button **Conectar NotebookLM**. Before that click, `#notebook-status` reads `Clique em Conectar para verificar sua sessão local do NotebookLM.`

The Companion must already be running on this computer. The README starts it with `FLASHCARDS_COMPANION_WEB_ORIGIN` set to the exact site origin. For the verification server that origin is `http://127.0.0.1:18721`.

## Driving it with Playwright

Start a Companion only when nothing is listening on 8766. Record its pid. Use `FLASHCARDS_COMPANION_DATA_DIR=/tmp/flashcards-verify/companion` so the Google profile is not the user's real data directory.

1. Sign in.
2. Listen for browser requests to `http://127.0.0.1:8766/`.
3. `page.getByRole("button", { name: "Conectar NotebookLM" }).click()`.
4. Wait until `#notebook-status` matches one of the three texts above.

The button calls `GET /v1/notebooklm/status` on the Companion with a bearer token from `POST /api/v1/auth/companion/token`. If the status is `login_required`, the page then calls `POST /v1/notebooklm/login`. Both requests must go to `127.0.0.1:8766`, not to the hosted origin.

Proof is the status text, `data-state`, whether `#generation-fields` is disabled, and the Companion request URLs. Screenshot the panel after the click.

## Gotchas

- Port 8766 is a singleton. If a Companion is already listening and this skill did not start it, stop. Driving it would use that process's Google profile.
- `GET /v1/health` without `Origin: http://127.0.0.1:18721` is rejected. The browser sends `Origin` on its own.
- A copied or missing Google session is not a successful connection. `Nenhuma conta do NotebookLM conectada.` is the honest end state when nobody has logged in.
- Do not use `frontend/e2e/run_companion.py`. That process answers with a fake NotebookLM.
