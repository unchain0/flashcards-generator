import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { basename } from "node:path";

test("uploads a synthetic document to the native Companion and downloads real Google cards", async ({
  page,
}, testInfo) => {
  const source = process.env["FLASHCARDS_LIVE_SOURCE_FILE"];
  if (!source) throw new Error("FLASHCARDS_LIVE_SOURCE_FILE is required.");
  const name = `01-${basename(source).replace(/\.(pdf|pptx)$/i, ".csv")}`;
  const hostedUploads: string[] = [];
  let localUpload = false;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() !== "POST" || !url.pathname.includes("/jobs")) return;
    if (url.origin === "http://127.0.0.1:8766") {
      localUpload = true;
      expect(request.headers()["content-type"]).toContain("multipart/form-data;");
    } else {
      hostedUploads.push(url.pathname);
    }
  });
  await page.goto("/");
  const password = page.getByLabel("Senha de acesso");
  await password.fill("e2e-password-123");
  await password.press("Enter");
  await expect(page.getByRole("heading", { name: "Nova geração" })).toBeVisible({
    timeout: 30_000,
  });
  const login =
    process.env["FLASHCARDS_LIVE_EXPECT_BROWSER_LOGIN"] === "true"
      ? page.waitForResponse(
          (response) =>
            response.url() === "http://127.0.0.1:8766/v1/notebooklm/login" &&
            response.request().method() === "POST",
          { timeout: 60_000 },
        )
      : undefined;
  await page.getByRole("button", { name: "Conectar NotebookLM" }).click();
  if (login) {
    const response = await login;
    expect(response.ok()).toBe(true);
    expect(await response.json()).toMatchObject({ authenticated: true, status: "authenticated" });
  }
  const files = page.getByLabel("Arquivos PDF ou PPTX");
  await expect(files).toBeEnabled({ timeout: 60_000 });
  await page.getByLabel("Quantidade", { exact: true }).selectOption("fewer");
  await page.getByLabel("Limite por arquivo (segundos)").fill("180");
  await files.setInputFiles(source);
  const terminal = page.waitForResponse(
    async (response) => {
      const url = new URL(response.url());
      if (
        url.origin === "http://127.0.0.1:8766" &&
        url.pathname === "/v1/jobs" &&
        response.request().method() === "POST"
      ) {
        expect(response.status()).toBe(202);
        return false;
      }
      if (
        url.origin !== "http://127.0.0.1:8766" ||
        !/^\/v1\/jobs\/[a-f0-9]{32}$/.test(url.pathname) ||
        response.request().method() !== "GET" ||
        !response.ok()
      )
        return false;
      const job: { status?: string } = await response.json();
      return job.status === "completed" || job.status === "failed" || job.status === "cancelled";
    },
    { timeout: 300_000 },
  );
  await page.getByRole("button", { name: "Gerar flashcards", exact: true }).click();
  const status = page.locator("#job-status");
  await terminal;
  await page.screenshot({
    path: testInfo.outputPath("native-generation-terminal.png"),
    fullPage: true,
  });
  await expect(status).toHaveAttribute("data-status", "completed");
  expect(localUpload).toBe(true);
  expect(hostedUploads).toEqual([]);
  const artifact = page.getByRole("button", { name: `Baixar ${name}`, exact: true });
  await expect(artifact).toBeVisible();
  const downloading = page.waitForEvent("download");
  await artifact.click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe(name);
  const path = await download.path();
  if (!path) throw new Error("Generated CSV download was not available.");
  const csv = readFileSync(path, "utf8");
  expect(csv).toMatch(/^"/);
  expect(csv).toContain("{{c1::");
  expect(csv.length).toBeGreaterThan(100);
  await page.screenshot({
    path: testInfo.outputPath("native-generation-completed.png"),
    fullPage: true,
  });
});
