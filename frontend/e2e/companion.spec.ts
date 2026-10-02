import { expect, test } from "@playwright/test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

test("gera pelo Companion real com o provedor NotebookLM simulado", async ({ page }) => {
  const directory = process.env["FLASHCARDS_E2E_DIRECTORY"];
  const commandLog = process.env["FLASHCARDS_E2E_COMPANION_COMMAND_LOG"];
  if (!directory || !commandLog) {
    throw new Error("O ambiente isolado do Companion não foi configurado.");
  }
  const remoteGenerationRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/v1/jobs")) {
      remoteGenerationRequests.push(request.url());
    }
  });

  await page.goto("/");
  await page.getByLabel("Senha de acesso").fill("e2e-password-123");
  await page.getByLabel("Senha de acesso").press("Enter");
  await expect(page.getByRole("heading", { name: "Nova geração" })).toBeVisible();
  await page.getByRole("button", { name: "Conectar NotebookLM" }).click();
  await expect(page.locator("#notebook-status")).toHaveText("NotebookLM conectado.");

  await page.getByLabel("Arquivos PDF ou PPTX").setInputFiles(join(directory, "lesson.pdf"));
  await page.getByRole("button", { name: "Gerar flashcards" }).click();
  await expect(page.locator("#job-status")).toHaveAttribute("data-status", "completed", {
    timeout: 20_000,
  });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /^Baixar .+\.csv$/ }).click();
  const download = await downloadPromise;
  const downloadedPath = await download.path();
  if (!downloadedPath) {
    throw new Error("O CSV gerado pelo Companion não foi baixado.");
  }
  const csv = readFileSync(downloadedPath, "utf8");
  expect(csv).toContain("{{c1::");
  expect(csv).toContain("instructional content");
  expect(download.suggestedFilename()).toMatch(/\.csv$/);
  expect(remoteGenerationRequests).toEqual([]);
  expect(existsSync(join(directory, "notebooklm-commands.jsonl"))).toBe(false);

  const commands = readFileSync(commandLog, "utf8");
  for (const command of [
    '["auth","check"]',
    '["login"]',
    '["generate","flashcards"',
    '["download","flashcards"',
  ]) {
    expect(commands).toContain(command);
  }
  const profilesDirectory = join(directory, "companion", "profiles");
  const profiles = readdirSync(profilesDirectory);
  expect(profiles).toHaveLength(1);
  expect(profiles[0]).toMatch(/^[a-f0-9]{32}$/);
  expect(statSync(join(profilesDirectory, profiles[0])).mode & 0o777).toBe(0o700);

  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page.getByRole("heading", { name: "Acesse o gerador" })).toBeVisible();
});
