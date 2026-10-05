import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import rust from "./playwright.rust.config";

const directory = dirname(fileURLToPath(import.meta.url));
const data = process.env["FLASHCARDS_LIVE_COMPANION_DATA_DIR"];
if (!data) throw new Error("FLASHCARDS_LIVE_COMPANION_DATA_DIR is required.");
const frontendPort = process.env["FLASHCARDS_E2E_FRONTEND_PORT"] ?? "5174";
const companion = resolve(
  directory,
  process.env["FLASHCARDS_LIVE_COMPANION_BIN_DIR"] ?? "../target/release",
  "flashcards-companion",
);

export default defineConfig({
  ...rust,
  testMatch: "live-generation.live.ts",
  workers: 1,
  retries: 0,
  timeout: 360_000,
  outputDir: "test-results/live-companion",
  webServer: [
    ...(Array.isArray(rust.webServer) ? rust.webServer : []),
    {
      command: `'${companion.replaceAll("'", "'\\''")}'`,
      cwd: directory,
      url: "http://127.0.0.1:8766/v1/health",
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
      env: {
        FLASHCARDS_COMPANION_WEB_ORIGIN: `http://127.0.0.1:${frontendPort}`,
        FLASHCARDS_COMPANION_DATA_DIR: data,
        FLASHCARDS_SENTRY_ENABLED: "false",
      },
    },
  ],
});
