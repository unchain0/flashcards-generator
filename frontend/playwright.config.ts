import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const backendPort = process.env["FLASHCARDS_E2E_BACKEND_PORT"] ?? "8123";
const frontendPort = process.env["FLASHCARDS_E2E_FRONTEND_PORT"] ?? "5174";
const configDirectory = dirname(fileURLToPath(import.meta.url));
const e2eDirectory =
  process.env["FLASHCARDS_E2E_DIRECTORY"] ??
  mkdtempSync(join(tmpdir(), "flashcards-generator-e2e-"));
const notebookLMCommandLog = join(e2eDirectory, "notebooklm-commands.jsonl");
const companionCommandLog = join(e2eDirectory, "companion-notebooklm-commands.jsonl");
process.env["FLASHCARDS_E2E_DIRECTORY"] = e2eDirectory;
process.env["FLASHCARDS_E2E_COMMAND_LOG"] = notebookLMCommandLog;
process.env["FLASHCARDS_E2E_COMPANION_COMMAND_LOG"] = companionCommandLog;

export default defineConfig({
  testDir: "./e2e",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    browserName: "chromium",
  },
  webServer: [
    {
      command: "uv run --frozen --project .. python e2e/run_backend.py",
      cwd: configDirectory,
      url: `http://127.0.0.1:${backendPort}/health/live`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        FLASHCARDS_ENVIRONMENT: "test",
        FLASHCARDS_HOST: "127.0.0.1",
        FLASHCARDS_PORT: backendPort,
        FLASHCARDS_DATABASE_URL: `sqlite+aiosqlite:///${join(e2eDirectory, "web.db")}`,
        FLASHCARDS_DATA_DIR: join(e2eDirectory, "data"),
        FLASHCARDS_BOOTSTRAP_PASSWORD: "e2e-password-123",
        FLASHCARDS_E2E_COMMAND_LOG: notebookLMCommandLog,
      },
    },
    {
      command: `pnpm exec vp dev --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      cwd: configDirectory,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        FLASHCARDS_API_ORIGIN: `http://127.0.0.1:${backendPort}`,
      },
    },
    {
      command: "uv run --frozen --project .. python e2e/run_companion.py",
      cwd: configDirectory,
      url: "http://127.0.0.1:8765/v1/health",
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
      env: {
        FLASHCARDS_COMPANION_WEB_ORIGIN: `http://127.0.0.1:${frontendPort}`,
        FLASHCARDS_COMPANION_DATA_DIR: join(e2eDirectory, "companion"),
        FLASHCARDS_E2E_COMMAND_LOG: companionCommandLog,
      },
    },
  ],
});
