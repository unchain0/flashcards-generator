# Sign out

The person ends the session. The dashboard closes, the login form returns, and the session cookie is cleared.

## Sub-features

- **Sair** returns to **Acesse o gerador** and `#auth-status` says `Acesso encerrado.`
- `POST /api/v1/auth/logout` returns 200 `{"authenticated":false}` and expires `flashcards_session`.
- The matching `web_sessions` row is deleted. A later `GET /api/v1/auth/me` returns 401 `{"status_code":401,"detail":"Authentication required"}`.

## How to get to it (user POV)

Sign in first. The button **Sair** is in the top corner of the dashboard. It is hidden on the login screen (`#logout-button` starts with the `hidden` attribute).

## Driving it with Playwright

After the sign-in end state:

1. `page.getByRole("button", { name: "Sair" }).click()`.
2. Wait for `POST /api/v1/auth/logout` to return 200.
3. Wait for `getByRole("heading", { name: "Acesse o gerador" })` and the text `Acesso encerrado.`
4. Confirm `#dashboard` is hidden, `#logout-button` is hidden, and the context has no `flashcards_session` cookie.
5. From the page, `fetch("/api/v1/auth/me")` returns 401.
6. `web_sessions` for the scratch database is 0 again. `web_users` stays 1.

Screenshot the login form with `Acesso encerrado.` visible, then the 401 from `/api/v1/auth/me`. Put both under `.cursor/skills/verify-flashcards/evidence/`.

## Gotchas

- Logout requires the session cookie. Calling it without a session still returns 200 and an empty cookie, but that does not prove a real session was revoked. Sign in first and watch the row count drop from 1 to 0.
- Do not delete rows from SQLite yourself. The proof is the button, then the empty session table.
