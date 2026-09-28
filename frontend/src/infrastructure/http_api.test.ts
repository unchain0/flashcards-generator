import { describe, expect, it, vi } from "vitest";
import type { GenerationJob } from "../domain/contracts";
import { HttpApiError, HttpFlashcardsApi } from "./http_api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("HttpFlashcardsApi", () => {
  it("sends only a short-lived bearer capability to the local companion", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const job: GenerationJob = {
      id: "job-123",
      status: "queued",
      message: "Arquivo recebido.",
      filenames: ["lesson.pdf"],
      discovered_sources: 0,
      completed_sources: 0,
      skipped_sources: 0,
      failed_sources: 0,
      artifacts: [],
      error: null,
    };
    const responseQueue = [
      jsonResponse({ authenticated: true }),
      jsonResponse({ authenticated: true }),
      jsonResponse({ authenticated: false }),
      jsonResponse({ access_token: "capability-1", expires_in: 300 }, 201),
      jsonResponse({
        authenticated: false,
        status: "login_required",
        message: "login required",
      }),
      jsonResponse({ access_token: "capability-2", expires_in: 300 }, 201),
      jsonResponse({ authenticated: true, status: "authenticated", message: "ok" }),
      jsonResponse({ access_token: "capability-3", expires_in: 300 }, 201),
      jsonResponse(job, 202),
      jsonResponse({ access_token: "capability-4", expires_in: 300 }, 201),
      jsonResponse({ ...job, error: "Falha local" }),
      jsonResponse({ access_token: "capability-5", expires_in: 300 }, 201),
      new Response("Front,Back\nterm,definition\n", {
        headers: { "content-type": "text/csv" },
      }),
      jsonResponse({ access_token: "capability-6", expires_in: 300 }, 201),
      jsonResponse({ detail: "Arquivo ausente" }, 404),
    ];
    const fetcher: typeof fetch = async (input, init) => {
      calls.push({ input, init });
      const response = responseQueue.shift();
      if (!response) {
        throw new Error("Resposta de teste ausente");
      }
      return response;
    };
    const api = new HttpFlashcardsApi(fetcher);
    const form = new FormData();
    form.append("files", new Blob(["pdf"]), "lesson.pdf");

    await expect(api.me()).resolves.toEqual({ authenticated: true });
    await expect(api.login("senha-segura-123")).resolves.toEqual({ authenticated: true });
    await expect(api.logout()).resolves.toEqual({ authenticated: false });
    await expect(api.notebookStatus()).resolves.toMatchObject({
      status: "login_required",
    });
    await expect(api.startNotebookLogin()).resolves.toMatchObject({
      authenticated: true,
    });
    await expect(api.createJob(form)).resolves.toMatchObject({ id: "job-123" });
    await expect(api.getJob("job/123")).resolves.toMatchObject({ id: "job-123" });
    await expect(api.download("/artifact.csv")).resolves.toBeInstanceOf(Blob);
    await expect(api.download("/missing.csv")).rejects.toMatchObject({
      message: "Arquivo ausente",
      status: 404,
    });

    expect(calls.map(({ input }) => input)).toEqual([
      "/api/v1/auth/me",
      "/api/v1/auth/login",
      "/api/v1/auth/logout",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8765/v1/notebooklm/status",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8765/v1/notebooklm/login",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8765/v1/jobs",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8765/v1/jobs/job%2F123",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8765/artifact.csv",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8765/missing.csv",
    ]);
    expect(calls[3]?.init?.credentials).toBe("same-origin");
    expect(calls[4]?.init?.credentials).toBe("omit");
    expect(calls[5]?.init?.credentials).toBe("same-origin");
    expect(calls[6]?.init?.credentials).toBe("omit");
    expect(new Headers(calls[4]?.init?.headers).get("authorization")).toBe("Bearer capability-1");
    expect(new Headers(calls[6]?.init?.headers).get("authorization")).toBe("Bearer capability-2");
    expect(
      calls
        .filter(({ input }) => String(input).startsWith("http://127.0.0.1:8765/"))
        .every(({ init }) => init?.credentials === "omit"),
    ).toBe(true);
    expect(new Headers(calls[1]?.init?.headers).get("content-type")).toBe("application/json");
    expect(new Headers(calls[8]?.init?.headers).has("content-type")).toBe(false);
    expect(calls[8]?.init?.body).toBe(form);
    expect(new Headers(calls[8]?.init?.headers).get("authorization")).toBe("Bearer capability-3");
  });

  it("explains when the local companion cannot be reached", async () => {
    const api = new HttpFlashcardsApi(async (_input, init) => {
      if (init?.method === "POST") {
        return jsonResponse({ access_token: "capability", expires_in: 300 }, 201);
      }
      throw new TypeError("Failed to fetch");
    });

    await expect(api.notebookStatus()).rejects.toMatchObject({
      name: "HttpApiError",
      message: expect.stringContaining("Inicie o auxiliar"),
      status: 0,
    });
  });

  it("preserves unexpected fetch failures and local authorization errors", async () => {
    const token = jsonResponse({ access_token: "capability", expires_in: 300 }, 201);
    const unexpected = new HttpFlashcardsApi(async (_input, init) => {
      if (init?.method === "POST") {
        return token.clone();
      }
      throw new Error("Falha inesperada");
    });
    const unauthorized = new HttpFlashcardsApi(async (_input, init) =>
      init?.method === "POST" ? token.clone() : jsonResponse({ detail: "Sessão expirada" }, 401),
    );

    await expect(unexpected.notebookStatus()).rejects.toThrow("Falha inesperada");
    await expect(unauthorized.notebookStatus()).rejects.toMatchObject({
      message: "Sessão expirada",
      status: 401,
    });
  });

  it("uses API details when available and falls back for invalid error bodies", async () => {
    const responseQueue = [
      jsonResponse({ detail: "Senha incorreta" }, 401),
      new Response("não é JSON", { status: 500 }),
      jsonResponse({ detail: 42 }, 503),
      jsonResponse(null, 502),
    ];
    const api = new HttpFlashcardsApi(async () => {
      const response = responseQueue.shift();
      if (!response) {
        throw new Error("Resposta de teste ausente");
      }
      return response;
    });

    await expect(api.me()).rejects.toMatchObject({
      name: "HttpApiError",
      message: "Senha incorreta",
      status: 401,
    } satisfies Partial<HttpApiError>);
    await expect(api.me()).rejects.toMatchObject({ message: "Falha HTTP 500" });
    await expect(api.me()).rejects.toMatchObject({ message: "Falha HTTP 503" });
    await expect(api.me()).rejects.toMatchObject({ message: "Falha HTTP 502" });
  });

  it("rejects malformed successful response bodies", async () => {
    const responseQueue = [jsonResponse(null), jsonResponse({ authenticated: "yes" })];
    const api = new HttpFlashcardsApi(async () => {
      const response = responseQueue.shift();
      if (!response) {
        throw new Error("Resposta de teste ausente");
      }
      return response;
    });

    await expect(api.me()).rejects.toMatchObject({
      message: "Resposta inválida de autenticação.",
      status: 502,
    });
    await expect(api.me()).rejects.toMatchObject({
      message: "Resposta inválida de autenticação.",
      status: 502,
    });
  });

  it("preserves unexpected failures while parsing an API error body", async () => {
    const parsingFailure = new Error("Leitura da resposta interrompida");
    const response = new Response("body", { status: 502 });
    vi.spyOn(response, "json").mockRejectedValue(parsingFailure);
    const api = new HttpFlashcardsApi(async () => response);

    await expect(api.me()).rejects.toBe(parsingFailure);
  });
});
