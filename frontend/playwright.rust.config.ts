import { defineConfig } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import reference from "./playwright.config";

const directory = dirname(fileURLToPath(import.meta.url));
const backendPort = process.env["FLASHCARDS_E2E_BACKEND_PORT"] ?? "8123";
const frontendPort = process.env["FLASHCARDS_E2E_FRONTEND_PORT"] ?? "5174";
const database = process.env["FLASHCARDS_TEST_DATABASE_URL"];
const binaryDirectory = process.env["FLASHCARDS_RUST_BIN_DIR"] ?? "../target/debug";
if (!database) throw new Error("FLASHCARDS_TEST_DATABASE_URL is required for Rust browser checks.");

export default defineConfig({
  ...reference,
  testMatch: "generation.spec.ts",
  webServer: [
    {
      command: `${binaryDirectory}/flashcards-web`,
      cwd: directory,
      url: `http://127.0.0.1:${backendPort}/health/live`,
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
      env: {
        FLASHCARDS_ENVIRONMENT: "test",
        FLASHCARDS_HOST: "127.0.0.1",
        FLASHCARDS_PORT: backendPort,
        FLASHCARDS_DATABASE_URL: database,
        FLASHCARDS_AUTO_CREATE_SCHEMA: "false",
        FLASHCARDS_BOOTSTRAP_PASSWORD: "e2e-password-123",
        FLASHCARDS_STATIC_DIR: resolve(
          directory,
          "../src/flashcards_generator/delivery/web/static/dist",
        ),
        FLASHCARDS_SENTRY_ENABLED: "false",
      },
    },
    {
      command: `pnpm exec vp dev --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      cwd: directory,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { FLASHCARDS_API_ORIGIN: `http://127.0.0.1:${backendPort}` },
    },
  ],
});
