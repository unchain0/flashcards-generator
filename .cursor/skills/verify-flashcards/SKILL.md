---
name: verify-flashcards
description: >-
  Launch and drive the Flashcards Generator hosted web UI the way a user does,
  and capture proof. Use when a change touches the dashboard, password sign-in,
  sign-out, NotebookLM connection, flashcard generation, or CSV download.
---

# Verify Flashcards Generator

The primary surface is the hosted web dashboard. A person opens it in a browser, signs in with a password, and uses the local Flashcards Companion for NotebookLM. There is no end-user CLI or TUI.

Secondary surfaces, not what this skill launches:

- The Companion HTTP API on `127.0.0.1:8766`. The dashboard calls it from the browser. The port is fixed in `frontend/src/infrastructure/http_api.ts`. Two Companions cannot run side by side. If something is already listening on 8766, do not start another and do not drive connection or generation against it.
- The Rust hosted server and Rust Companion. Production Compose uses `Dockerfile.rust`. This skill follows the documented local server, `uv run flashcards-web`, which serves the same built dashboard.

The verification server is a separate instance. It binds `127.0.0.1:18721`, uses a scratch SQLite file under `/tmp/flashcards-verify/`, and a bootstrap password that exists only in that file. It does not use port 8000, `./flashcards.db`, or a user's `.env` secrets. Do not point it at a shared database.

## Launch

From the repo root, with `uv` on `PATH` (or at `~/.local/bin/uv`):

```bash
uv sync --frozen
corepack pnpm@12.5.1 --dir frontend install --frozen-lockfile
corepack pnpm@12.5.1 --dir frontend run build
corepack pnpm@12.5.1 --dir frontend exec playwright install chromium
bash .cursor/skills/verify-flashcards/scripts/launch.sh
```

`launch.sh` repeats the sync and build only when `.venv` or `src/flashcards_generator/delivery/web/static/dist/index.html` is missing. The build output is the dashboard the server serves. The app refuses to start without that directory (`Frontend compilado ausente` in `delivery/web/app.py`).

The server command the helper runs, with its environment, is:

```bash
FLASHCARDS_ENVIRONMENT=development \
FLASHCARDS_HOST=127.0.0.1 \
FLASHCARDS_PORT=18721 \
FLASHCARDS_DATABASE_URL=sqlite+aiosqlite:////tmp/flashcards-verify/web.db \
FLASHCARDS_DATA_DIR=/tmp/flashcards-verify/data \
FLASHCARDS_BOOTSTRAP_PASSWORD=verify-local-pass \
FLASHCARDS_SESSION_SECRET=verify-session-secret-32-characters-min \
FLASHCARDS_AUTH_LOOKUP_SECRET=verify-lookup-secret-32-characters-minx \
FLASHCARDS_AUTO_CREATE_SCHEMA=true \
FLASHCARDS_SENTRY_ENABLED=false \
uv run --frozen flashcards-web
```

`verify-local-pass` is the password for this scratch database only. It is 16 characters, inside the app's 12–256 limit. It is not a user account on any other database.

Ready means `GET http://127.0.0.1:18721/health/ready` returns `{"status":"ok"}`. The helper waits up to 30 seconds and prints `ready http://127.0.0.1:18721 pid=<pid>`. The pid and password are written to `/tmp/flashcards-verify/state.env`. The server log is `/tmp/flashcards-verify/server.log`.

If that pid is already alive, the helper refuses to start a second copy. If port 18721 is taken by any other process, it exits without killing that process.

Sign-in does not need the Companion. Start a Companion only when driving connection or generation, and only when 8766 is free:

```bash
FLASHCARDS_COMPANION_WEB_ORIGIN=http://127.0.0.1:18721 \
FLASHCARDS_COMPANION_DATA_DIR=/tmp/flashcards-verify/companion \
uv run --frozen flashcards-companion
```

Its ready check is `GET http://127.0.0.1:8766/v1/health` with header `Origin: http://127.0.0.1:18721`, body `{"status":"ok"}`. A request without that exact origin is rejected. Record that process's pid in the scratch directory and stop it in cleanup. Do not use `frontend/e2e/run_companion.py` or `e2e/fake-bin`; those replace NotebookLM with a test double.

## Doctor

Run this before driving, and whenever the page looks wrong:

```bash
bash .cursor/skills/verify-flashcards/scripts/doctor.sh
```

It is read-only. It passes only when all of these are true:

- The pid in `/tmp/flashcards-verify/state.env` is alive.
- Port 18721 is listened on by that pid or a descendant of it.
- `GET /health/live` and `GET /health/ready` both return `{"status":"ok"}`.
- `GET /` contains `Gere flashcards do seu material.`

A passing doctor prints `doctor: ok http://127.0.0.1:18721 pid=<pid>`. Anything else prints `doctor: not ready:` and a reason. Do not drive a server that fails this check, and do not attach to port 8000 to compensate.

## Drive

Use Playwright from `frontend` (`@playwright/test` 1.63.0) against `BASE_URL` in the state file. Headless Chromium is enough. The repo's browser specs in `frontend/e2e/` show the same handles; run those specs only as a regression suite. A verification proof drives the server from Launch, not `e2e/run_backend.py`.

Stable handles on `frontend/index.html`:

| Handle | What the user sees |
| --- | --- |
| `getByLabel("Senha de acesso")` | `#access-password` |
| `getByRole("button", { name: "Entrar" })` | `#login-button` |
| `#auth-status` | login errors |
| `getByRole("heading", { name: "Acesse o gerador" })` | signed-out panel |
| `getByRole("heading", { name: "Nova geração" })` | signed-in generation form |
| `getByRole("button", { name: "Sair" })` | `#logout-button` |
| `getByRole("button", { name: "Conectar NotebookLM" })` | `#connect-notebooklm` |
| `#notebook-status` | connection text, `data-state` `connected`, `disconnected`, or `error` |
| `#generation-fields` | disabled until NotebookLM is authenticated |
| `getByLabel("Arquivos PDF ou PPTX")` | `#files` |
| `getByRole("button", { name: "Gerar flashcards" })` | `#start-generation` |
| `#job-status` | `data-status` `queued`, `running`, `completed`, `failed`, or `cancelled` |
| `getByRole("button", { name: /^Baixar .+\.csv$/ })` | a finished CSV |

Sign-in, the feature proved with this skill:

```bash
node .cursor/skills/verify-flashcards/scripts/drive-login.mjs
```

The script reads the state file, runs doctor, then:

1. Opens `BASE_URL` and waits for the heading `Gere flashcards do seu material.`
2. Submits `not-the-password` with **Entrar**. The UI must show `Senha inválida. Confira e tente novamente.` and stay on `Acesse o gerador`. `POST /api/v1/auth/login` returns 401. `web_sessions` stays empty and no `flashcards_session` cookie is set.
3. Submits `verify-local-pass` with **Entrar**. `POST /api/v1/auth/login` returns 200 `{"authenticated":true}`. The heading `Nova geração` and the button **Sair** are visible, `#auth-panel` is hidden, and `#generation-fields` stays disabled.
4. Checks the cookie `flashcards_session` is `HttpOnly` and `SameSite=Lax` (not `Secure` in development), that `web_sessions` has one row, and that `GET /api/v1/auth/me` from the page returns 200 `{"authenticated":true}`. Generation stays off: the `#generation-fields` DOM `disabled` property is true, and both **Arquivos PDF ou PPTX** and **Gerar flashcards** report disabled. Playwright's `isDisabled()` is false for the fieldset element itself; use the file input, the submit button, or the fieldset's `disabled` property.

The other features are mapped under `features/`. Drive those the same way, through the browser, when the change touches them. A proof of sign-in does not cover them.

## Evidence

Write proof into `.cursor/skills/verify-flashcards/evidence/`. That directory is outside the scratch server and must survive cleanup.

For sign-in, `drive-login.mjs` writes:

- `01-login-screen.png` before any submit
- `02-password-rejected.png` after the rejected password, with the error visible
- `03-dashboard-after-login.png` after the dashboard replaces the form
- `proof.json` with the login status codes, the UI flags, cookie flags (not the cookie value), and `web_users` / `web_sessions` counts before, after the rejection, and after success
- `transcript.json` with the two login responses

Standards:

- Use the password form and the dashboard. Do not insert session rows, call internal setters, or hit test-only routes.
- Capture the action and the next state: the rejected attempt and the accepted attempt, not only the final screenshot.
- Check the side effect in SQLite (`web_users`, `web_sessions`) and `GET /api/v1/auth/me`, along with what is on screen. Do not copy the database into evidence; it holds password hashes.
- Do not record the session cookie value.
- The hosted app never receives PDF or PPTX bytes. Generation proof has to show the browser talking to `127.0.0.1:8766` and a downloaded CSV, not a hosted upload.
- NotebookLM is a real external system. Do not substitute `frontend/e2e` fakes and call that a generation proof. If no Google session is available, stop at the connection result you can see and say so.

## Cleanup

```bash
bash .cursor/skills/verify-flashcards/scripts/cleanup.sh
```

The helper reads the pid from `/tmp/flashcards-verify/state.env`, signals that pid and its descendants, and deletes `/tmp/flashcards-verify` (the database, log, and state file). It does not delete `.cursor/skills/verify-flashcards/evidence/`. It does not signal processes by name, and it does not stop a server on port 8000 or a Companion it did not start.

If you started a Companion yourself, stop that pid the same way, and delete only `/tmp/flashcards-verify/companion` if it is still there. Leave the user's Companion data directory alone.

Run cleanup after a failed drive too, so the next launch is not attached to a half-started process.

## Helpers

| Script | Invocation |
| --- | --- |
| `scripts/lib.sh` | Sourced by the shell helpers. Do not run it directly. |
| `scripts/launch.sh` | `bash .cursor/skills/verify-flashcards/scripts/launch.sh` |
| `scripts/doctor.sh` | `bash .cursor/skills/verify-flashcards/scripts/doctor.sh` |
| `scripts/cleanup.sh` | `bash .cursor/skills/verify-flashcards/scripts/cleanup.sh` |
| `scripts/drive-login.mjs` | `node .cursor/skills/verify-flashcards/scripts/drive-login.mjs` |

The shell helpers are executable. `drive-login.mjs` loads Playwright from `frontend/node_modules` and `uv` from `PATH` or `~/.local/bin/uv`.
