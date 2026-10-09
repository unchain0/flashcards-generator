# Sign in

The person types an access password and enters the generator. The app does not ask for a name or email. A correct password opens the dashboard and stores an HttpOnly session cookie.

## Sub-features

- Reject an unknown password. The form stays on **Acesse o gerador**, `#auth-status` says `Senha inválida. Confira e tente novamente.`, and no session is stored.
- Accept the password provisioned for this instance. The login form is hidden and **Nova geração** is shown. Generation stays disabled until NotebookLM is connected.
- An existing session skips the form. Reloading the page calls `GET /api/v1/auth/me` and, on 200, shows the dashboard again.

## How to get to it (user POV)

Open the verification origin from Launch (`http://127.0.0.1:18721/`). The first screen is the heading **Gere flashcards do seu material.** and the section **Acesse o gerador**, with the field **Senha de acesso** and the button **Entrar**.

## Driving it with Playwright

`node .cursor/skills/verify-flashcards/scripts/drive-login.mjs` drives the rejection and the successful sign-in. The same steps by hand:

1. `page.goto(BASE_URL)` and wait for `getByRole("heading", { name: "Gere flashcards do seu material." })`.
2. `getByLabel("Senha de acesso").fill("not-the-password")`, then `getByRole("button", { name: "Entrar" }).click()`. Wait for `POST /api/v1/auth/login` to return 401 and for the text `Senha inválida. Confira e tente novamente.`
3. Fill **Senha de acesso** with the `PASSWORD` value from `/tmp/flashcards-verify/state.env` (`verify-local-pass` for the scratch database) and click **Entrar** again. Wait for `POST /api/v1/auth/login` to return 200 `{"authenticated":true}` and for `getByRole("heading", { name: "Nova geração" })`.

Confirm `web_sessions` is 0 after the rejection and 1 after success. `web_users` is already 1 because launch provisioned the bootstrap password. From the page, `fetch("/api/v1/auth/me", { credentials: "same-origin" })` returns 200 `{"authenticated":true}`. The cookie name is `flashcards_session`; it is `HttpOnly` and `SameSite=Lax`.

## Gotchas

- The form resets the password field as soon as it is submitted, including while the request is in flight. Read the result from `#auth-status` or the dashboard, not from the input value.
- A password shorter than 12 characters cannot be provisioned. Login of a wrong password of any length up to 256 still returns 401 `Senha inválida`.
- The scratch password does not work on port 8000 or on `./flashcards.db`.
- Playwright's `locator.isDisabled()` is false for `#generation-fields` even when the fieldset is disabled. Check the fieldset's `disabled` property, or `isDisabled()` on **Arquivos PDF ou PPTX** and **Gerar flashcards**.
- Do not write the cookie value into evidence.
