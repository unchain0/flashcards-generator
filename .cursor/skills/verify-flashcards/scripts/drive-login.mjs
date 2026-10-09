#!/usr/bin/env node
/**
 * Drive the sign-in feature against the verification server.
 * Requires scripts/launch.sh and a Chromium install for frontend Playwright.
 */
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const skillDir = dirname(dirname(fileURLToPath(import.meta.url)));
const repoRoot = dirname(dirname(dirname(skillDir)));
const evidenceDir = join(skillDir, "evidence");
const statePath = process.env.VERIFY_STATE ?? "/tmp/flashcards-verify/state.env";

function loadState(path) {
  const entries = readFileSync(path, "utf8")
    .split("\n")
    .filter((line) => line.includes("="))
    .map((line) => {
      const index = line.indexOf("=");
      return [line.slice(0, index), line.slice(index + 1)];
    });
  return Object.fromEntries(entries);
}

function uvBin() {
  const local = join(process.env.HOME ?? "", ".local/bin/uv");
  if (existsSync(local)) {
    return local;
  }
  return "uv";
}

function counts(databasePath) {
  const script = `
import sqlite3, sys
con = sqlite3.connect(sys.argv[1], timeout=5)
print(con.execute("select count(*) from web_users").fetchone()[0])
print(con.execute("select count(*) from web_sessions").fetchone()[0])
`;
  const result = spawnSync(
    uvBin(),
    ["run", "--frozen", "--project", repoRoot, "python", "-c", script, databasePath],
    { cwd: repoRoot, encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr || "sqlite count failed");
  }
  const [users, sessions] = result.stdout.trim().split("\n").map((value) => Number(value));
  return { users, sessions };
}

const require = createRequire(join(repoRoot, "frontend/package.json"));
const { chromium } = require("@playwright/test");
const state = loadState(statePath);
const doctor = spawnSync(join(skillDir, "scripts/doctor.sh"), { encoding: "utf8" });
if (doctor.status !== 0) {
  process.stderr.write(doctor.stderr || doctor.stdout);
  process.exit(doctor.status ?? 1);
}

mkdirSync(evidenceDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const transcript = [];

try {
  await page.goto(state.BASE_URL, { waitUntil: "domcontentloaded" });
  await page.getByRole("heading", { name: "Gere flashcards do seu material." }).waitFor();
  await page.screenshot({ path: join(evidenceDir, "01-login-screen.png"), fullPage: true });

  const before = counts(state.DATABASE_PATH);
  if (before.users !== 1 || before.sessions !== 0) {
    throw new Error(`expected 1 user and 0 sessions before login, saw ${JSON.stringify(before)}`);
  }

  const rejected = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/auth/login") && response.request().method() === "POST",
  );
  await page.getByLabel("Senha de acesso").fill("not-the-password");
  await page.getByRole("button", { name: "Entrar" }).click();
  const rejectedResponse = await rejected;
  const rejectedBody = await rejectedResponse.json();
  transcript.push({
    action: "submit wrong password",
    status: rejectedResponse.status(),
    body: rejectedBody,
  });
  if (rejectedResponse.status() !== 401) {
    throw new Error(`wrong password returned ${rejectedResponse.status()}`);
  }
  await page.getByText("Senha inválida. Confira e tente novamente.").waitFor();
  await page.getByRole("heading", { name: "Acesse o gerador" }).waitFor();
  const afterRejection = counts(state.DATABASE_PATH);
  if (afterRejection.sessions !== 0) {
    throw new Error("wrong password created a session row");
  }
  const cookiesAfterRejection = await page.context().cookies();
  if (cookiesAfterRejection.some((cookie) => cookie.name === "flashcards_session")) {
    throw new Error("wrong password set a session cookie");
  }
  await page.screenshot({ path: join(evidenceDir, "02-password-rejected.png"), fullPage: true });

  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/auth/login") && response.request().method() === "POST",
  );
  await page.getByLabel("Senha de acesso").fill(state.PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();
  const acceptedResponse = await accepted;
  const acceptedBody = await acceptedResponse.json();
  transcript.push({
    action: "submit provisioned password",
    status: acceptedResponse.status(),
    body: acceptedBody,
  });
  if (acceptedResponse.status() !== 200 || acceptedBody.authenticated !== true) {
    throw new Error(`login failed: ${acceptedResponse.status()} ${JSON.stringify(acceptedBody)}`);
  }
  await page.getByRole("heading", { name: "Nova geração" }).waitFor();
  await page.getByRole("button", { name: "Sair" }).waitFor();
  const authHidden = await page.locator("#auth-panel").isHidden();
  const dashboardVisible = await page.locator("#dashboard").isVisible();
  const notebookStatus = await page.locator("#notebook-status").innerText();
  const generationDisabled = await page
    .locator("#generation-fields")
    .evaluate((element) => element instanceof HTMLFieldSetElement && element.disabled);
  const filesDisabled = await page.getByLabel("Arquivos PDF ou PPTX").isDisabled();
  const generateDisabled = await page.getByRole("button", { name: "Gerar flashcards" }).isDisabled();
  if (!authHidden || !dashboardVisible || !generationDisabled || !filesDisabled || !generateDisabled) {
    throw new Error(
      `dashboard did not replace the login form: ${JSON.stringify({
        authHidden,
        dashboardVisible,
        generationDisabled,
        filesDisabled,
        generateDisabled,
      })}`,
    );
  }
  if (notebookStatus !== "Clique em Conectar para verificar sua sessão local do NotebookLM.") {
    throw new Error(`unexpected notebook status: ${notebookStatus}`);
  }

  const sessionCookie = (await page.context().cookies()).find(
    (cookie) => cookie.name === "flashcards_session",
  );
  if (!sessionCookie?.httpOnly || sessionCookie.sameSite !== "Lax" || sessionCookie.secure) {
    throw new Error("session cookie is missing the development HttpOnly Lax flags");
  }
  const afterLogin = counts(state.DATABASE_PATH);
  if (afterLogin.users !== 1 || afterLogin.sessions !== 1) {
    throw new Error(`expected 1 session after login, saw ${JSON.stringify(afterLogin)}`);
  }
  const me = await page.evaluate(async () => {
    const response = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
    return { status: response.status, body: await response.json() };
  });
  if (me.status !== 200 || me.body.authenticated !== true) {
    throw new Error(`authenticated /api/v1/auth/me failed: ${JSON.stringify(me)}`);
  }
  await page.screenshot({
    path: join(evidenceDir, "03-dashboard-after-login.png"),
    fullPage: true,
  });

  const proof = {
    feature: "sign-in",
    baseUrl: state.BASE_URL,
    wrongPassword: transcript[0],
    login: transcript[1],
    ui: {
      authPanelHidden: authHidden,
      dashboardVisible,
      notebookStatus,
      generationFieldsDisabled: generationDisabled,
      fileInputDisabled: filesDisabled,
      generateButtonDisabled: generateDisabled,
    },
    sessionCookie: {
      present: true,
      httpOnly: sessionCookie.httpOnly,
      sameSite: sessionCookie.sameSite,
      secure: sessionCookie.secure,
    },
    database: { before, afterRejection, afterLogin },
    me,
  };
  writeFileSync(join(evidenceDir, "proof.json"), `${JSON.stringify(proof, null, 2)}\n`);
  writeFileSync(join(evidenceDir, "transcript.json"), `${JSON.stringify(transcript, null, 2)}\n`);
  process.stdout.write(`evidence: ${evidenceDir}\n`);
} finally {
  await browser.close();
}
