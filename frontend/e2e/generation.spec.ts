import { expect, test } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";

const companionError =
  "O Flashcards Companion não respondeu. Inicie o auxiliar e autorize o acesso local no navegador.";

test("autentica, gera localmente e baixa o CSV pelo navegador", async ({ page }, testInfo) => {
  const companionRequests: string[] = [];
  const remoteGenerationRequests: string[] = [];
  const browserErrors: string[] = [];
  let uploadContentType = "";
  let generationPayload = "";
  const expectedAnonymousAuthResponse =
    "Failed to load resource: the server responded with a status of 401 (Unauthorized)";
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && message.text() !== expectedAnonymousAuthResponse) {
      browserErrors.push(message.text());
    }
  });
  await page.route("**/api/v1/jobs**", async (route) => {
    remoteGenerationRequests.push(route.request().url());
    await route.abort();
  });
  await page.route("http://127.0.0.1:8765/**", async (route) => {
    const request = route.request();
    const allowOrigin = request.headers()["origin"] ?? "null";
    companionRequests.push(`${request.method()} ${request.url()}`);
    if (request.method() === "OPTIONS") {
      await route.fulfill({
        status: 204,
        headers: {
          "access-control-allow-origin": allowOrigin,
          "access-control-allow-methods": "GET, POST",
          "access-control-allow-headers": "authorization,content-type",
        },
      });
      return;
    }
    const path = new URL(request.url()).pathname;
    if (path === "/v1/jobs" && request.method() === "POST") {
      uploadContentType = request.headers()["content-type"] ?? "";
      generationPayload = request.postData() ?? "";
      await route.fulfill({
        status: 202,
        json: {
          id: "job-123",
          status: "queued",
          message: "Aguardando o início da geração.",
          filenames: ["lesson.pdf"],
          discovered_sources: 0,
          completed_sources: 0,
          skipped_sources: 0,
          failed_sources: 0,
          artifacts: [],
          error: null,
        },
        headers: { "access-control-allow-origin": allowOrigin },
      });
      return;
    }
    if (path === "/v1/jobs/job-123") {
      await route.fulfill({
        json: {
          id: "job-123",
          status: "completed",
          message: "Geração concluída.",
          filenames: ["lesson.pdf"],
          discovered_sources: 1,
          completed_sources: 1,
          skipped_sources: 0,
          failed_sources: 0,
          artifacts: [
            {
              name: "lesson.csv",
              url: "/v1/jobs/job-123/artifacts/lesson.csv",
            },
          ],
          error: null,
        },
        headers: { "access-control-allow-origin": allowOrigin },
      });
      return;
    }
    if (path === "/v1/jobs/job-123/artifacts/lesson.csv") {
      await route.fulfill({
        body: "Text,Extra\n{{c1::lesson}},review\n",
        headers: {
          "access-control-allow-origin": allowOrigin,
          "content-type": "text/csv",
        },
      });
      return;
    }
    const authenticated = path.endsWith("/notebooklm/login");
    await route.fulfill({
      json: {
        authenticated,
        status: authenticated ? "authenticated" : "login_required",
        message: authenticated ? "authenticated" : "login required",
      },
      headers: { "access-control-allow-origin": allowOrigin },
    });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /gere flashcards/i })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("login.png"), fullPage: true });
  const skipLink = page.getByRole("link", { name: "Pular para o conteúdo" });
  await page.keyboard.press("Tab");
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toHaveCSS("outline-style", "solid");
  await page.keyboard.press("Tab");
  const password = page.getByLabel("Senha de acesso");
  await expect(password).toBeFocused();
  await password.fill("e2e-password-123");
  await password.press("Enter");
  await expect(page.getByRole("heading", { name: "Nova geração" })).toBeVisible();
  await expect(password).toHaveValue("");
  await expect(page.locator("#notebook-status")).toHaveText(
    "Clique em Conectar para verificar sua sessão local do NotebookLM.",
  );
  await expect(page.locator("#app-status")).toHaveText("");
  await page.screenshot({
    path: testInfo.outputPath("dashboard-before-connect.png"),
    fullPage: true,
  });
  expect(companionRequests).toEqual([]);

  const fileInput = page.getByLabel("Arquivos PDF ou PPTX");
  const generateButton = page.getByRole("button", { name: "Gerar flashcards" });
  await expect(fileInput).toBeDisabled();
  await expect(page.locator("#difficulty")).toHaveCSS("opacity", "0.72");
  await expect(generateButton).toBeDisabled();
  await expect(generateButton).toHaveCSS("background-color", "rgb(22, 45, 69)");
  await expect(generateButton).toHaveCSS("color", "rgb(200, 215, 229)");
  await expect(generateButton).toHaveCSS("cursor", "not-allowed");
  await expect(page.locator("#job-status")).toBeHidden();

  await page.getByRole("button", { name: "Conectar NotebookLM" }).click();
  await expect(page.locator("#notebook-status")).toHaveText("NotebookLM conectado.");
  await expect(page.locator("#generation-legend")).toHaveText(
    "Escolha os arquivos para configurar a geração.",
  );
  await page.screenshot({ path: testInfo.outputPath("dashboard-connected.png"), fullPage: true });
  expect(companionRequests.some((item) => item.endsWith("/notebooklm/status"))).toBe(true);
  expect(companionRequests.some((item) => item.endsWith("/notebooklm/login"))).toBe(true);

  await expect(fileInput).toBeEnabled();
  await expect(generateButton).toBeDisabled();
  await page.getByLabel("Perfil de geração").selectOption("english-context");
  await page
    .getByLabel("Instruções adicionais (opcional)")
    .fill("Meu nível é iniciante; evite expressões que já conheço.");
  await page.screenshot({
    path: testInfo.outputPath("dashboard-english-profile.png"),
    fullPage: true,
  });
  await fileInput.setInputFiles({
    name: "lesson.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7 local fixture"),
  });
  await expect(fileInput).toBeEnabled();
  await expect(generateButton).toBeEnabled();
  await generateButton.click();
  await expect(page.locator("#job-status")).toHaveAttribute("data-status", "completed");
  await expect(page.getByRole("button", { name: "Baixar lesson.csv" })).toBeVisible();
  expect(uploadContentType).toContain("multipart/form-data; boundary=");
  expect(generationPayload).toContain("frases completas e naturais em inglês");
  expect(generationPayload).toContain("Meu nível é iniciante; evite expressões que já conheço.");
  expect(generationPayload).not.toContain("study_profile");
  expect(remoteGenerationRequests).toEqual([]);
  expect(
    companionRequests.some((item) => item.endsWith("POST http://127.0.0.1:8765/v1/jobs")),
  ).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("generation-completed.png"), fullPage: true });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Baixar lesson.csv" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("lesson.csv");
  const downloadedPath = await download.path();
  expect(downloadedPath).not.toBeNull();
  if (downloadedPath) {
    expect(readFileSync(downloadedPath, "utf8")).toBe("Text,Extra\n{{c1::lesson}},review\n");
  }

  const commandLog = process.env["FLASHCARDS_E2E_COMMAND_LOG"];
  expect(commandLog).toBeDefined();
  if (commandLog) {
    expect(existsSync(commandLog)).toBe(false);
  }

  for (const width of [195, 320, 390, 620, 821, 1024, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    const layout = await page.locator("body").evaluate((body) => {
      const sectionHeading = body.querySelector("h2");
      const bodyFontSize = Number.parseFloat(getComputedStyle(body).fontSize);
      return {
        client: body.clientWidth,
        scroll: body.scrollWidth,
        headingClient: body.querySelector("h1")?.clientWidth ?? 0,
        headingScroll: body.querySelector("h1")?.scrollWidth ?? 0,
        displayHeadingScale: body.querySelector("h2")
          ? Number.parseFloat(getComputedStyle(body.querySelector("h1") ?? body).fontSize) /
            Number.parseFloat(getComputedStyle(body.querySelector("h2") ?? body).fontSize)
          : 0,
        sectionHeadingScale: sectionHeading
          ? Number.parseFloat(getComputedStyle(sectionHeading).fontSize) / bodyFontSize
          : 0,
        fileControl: body.querySelector<HTMLElement>(".file-control")
          ? {
              client: body.querySelector<HTMLElement>(".file-control")?.clientWidth ?? 0,
              scroll: body.querySelector<HTMLElement>(".file-control")?.scrollWidth ?? 0,
              fontSize: Number.parseFloat(
                getComputedStyle(body.querySelector<HTMLElement>(".file-control") ?? body).fontSize,
              ),
            }
          : { client: 0, scroll: 0, fontSize: 0 },
        controls: Array.from(body.querySelectorAll("input, button"))
          .filter(
            (control) =>
              control.getClientRects().length > 0 &&
              !control.classList.contains("file-control__input"),
          )
          .map((control) => control.getBoundingClientRect().height),
        overflow: Array.from(body.querySelectorAll("*"))
          .map((element) => ({
            element: `${element.tagName.toLowerCase()}#${element.id}.${element.className.toString()}`,
            right: Math.round(element.getBoundingClientRect().right),
            width: Math.round(element.getBoundingClientRect().width),
            scroll: element.scrollWidth,
            client: element.clientWidth,
          }))
          .filter((element) => element.right > body.clientWidth || element.scroll > element.client),
      };
    });
    expect(layout.scroll, JSON.stringify(layout.overflow)).toBeLessThanOrEqual(layout.client);
    expect(layout.headingScroll, JSON.stringify(layout.overflow)).toBeLessThanOrEqual(
      layout.headingClient,
    );
    expect(layout.displayHeadingScale).toBeGreaterThanOrEqual(1.25);
    expect(layout.sectionHeadingScale).toBeGreaterThanOrEqual(1.25);
    expect(layout.fileControl.scroll).toBeLessThanOrEqual(layout.fileControl.client);
    expect(layout.fileControl.fontSize).toBeGreaterThanOrEqual(14);
    expect(layout.controls.every((height) => height >= 44)).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`dashboard-${width}.png`),
      fullPage: true,
    });
  }

  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page.getByRole("heading", { name: "Acesse o gerador" })).toBeVisible();
  expect(browserErrors).toEqual([]);
});

test("mantém a tela de acesso legível em uma tela estreita", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: /gere flashcards/i })).toBeVisible();
  await expect(page.getByLabel("Senha de acesso")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("login-mobile.png"), fullPage: true });
  const widths = await page.locator("body").evaluate((body) => ({
    client: body.clientWidth,
    scroll: body.scrollWidth,
  }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);
});

test("explica quando o auxiliar local não responde", async ({ page }, testInfo) => {
  await page.route("http://127.0.0.1:8765/**", (route) => route.abort());
  await page.goto("/");
  await page.getByLabel("Senha de acesso").fill("e2e-password-123");
  await page.getByLabel("Senha de acesso").press("Enter");
  await expect(page.getByRole("heading", { name: "Nova geração" })).toBeVisible();
  const connectButton = page.getByRole("button", { name: "Conectar NotebookLM" });
  await connectButton.click();

  await expect(page.locator("#notebook-status")).toHaveText(companionError);
  await expect(page.locator("#notebook-status")).toHaveAttribute("data-state", "error");
  await expect(page.locator("#notebook-status")).toHaveCSS("color", "rgb(255, 152, 136)");
  await page.screenshot({ path: testInfo.outputPath("companion-unavailable.png"), fullPage: true });
  await expect(connectButton).toBeEnabled();
});

test("mantém o link de salto fora da tela até receber foco", async ({ page }) => {
  await page.setViewportSize({ width: 195, height: 844 });
  await page.goto("/");

  const skipLink = page.getByRole("link", { name: "Pular para o conteúdo" });
  const bounds = await skipLink.boundingBox();
  expect(bounds).not.toBeNull();
  if (bounds === null) {
    throw new Error("O link de salto não está presente.");
  }
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(0);

  await page.keyboard.press("Tab");
  await expect(skipLink).toBeFocused();
});
