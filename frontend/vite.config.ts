import { defineConfig } from "vite-plus";

const apiOrigin = process.env["FLASHCARDS_API_ORIGIN"] ?? "http://127.0.0.1:8000";

export default defineConfig({
  build: {
    outDir: "../src/flashcards_generator/delivery/web/static/dist",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": apiOrigin,
      "/health": apiOrigin,
    },
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "jsdom",
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: ["src/**/*.test.ts"],
      reporter: ["text", "lcov"],
      thresholds: {
        perFile: true,
        autoUpdate: false,
        branches: 100,
        functions: 100,
        lines: 100,
        statements: 100,
      },
    },
  },
});
