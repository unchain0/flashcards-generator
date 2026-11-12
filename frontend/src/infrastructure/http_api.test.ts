import { describe, expect, it, vi } from "vite-plus/test";
import { HttpApiError } from "../domain/contracts";
import { HttpFlashcardsApi } from "./http_api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("HttpFlashcardsApi", () => {
  it("keeps companion capabilities short-lived and uses the server for generation", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
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
      jsonResponse({ job_id: "job-1" }, 202),
      jsonResponse({
        status: "done",
        cards: 5,
        download_url: "/api/v1/jobs/job-1/csv",
      }),
      new Response("Front,Back\r\nterm,definition\r\n", {
        headers: { "content-type": "text/csv" },
      }),
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
    form.append("file", new Blob(["pdf"]), "lesson.pdf");

    await expect(api.me()).resolves.toEqual({ authenticated: true });
    await expect(api.login("senha-segura-123")).resolves.toEqual({ authenticated: true });
    await expect(api.logout()).resolves.toEqual({ authenticated: false });
    await expect(api.notebookStatus()).resolves.toMatchObject({
      status: "login_required",
    });
    await expect(api.startNotebookLogin()).resolves.toMatchObject({
      authenticated: true,
    });
    await expect(api.createServerJob(form)).resolves.toEqual({ job_id: "job-1" });
    await expect(api.getServerJob("job/1")).resolves.toMatchObject({ cards: 5 });
    await expect(api.downloadServer("/api/v1/jobs/job-1/csv")).resolves.toBeInstanceOf(Blob);
    await expect(api.downloadServer("/missing.csv")).rejects.toMatchObject({
      message: "Arquivo ausente",
      status: 404,
    });

    expect(calls.map(({ input }) => input)).toEqual([
      "/api/v1/auth/me",
      "/api/v1/auth/login",
      "/api/v1/auth/logout",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8766/v1/notebooklm/status",
      "/api/v1/auth/companion/token",
      "http://127.0.0.1:8766/v1/notebooklm/login",
      "/api/v1/generate",
      "/api/v1/jobs/job%2F1",
      "/api/v1/jobs/job-1/csv",
      "/missing.csv",
    ]);
    expect(calls[3]?.init?.credentials).toBe("same-origin");
    expect(calls[4]?.init?.credentials).toBe("omit");
    expect(new Headers(calls[4]?.init?.headers).get("authorization")).toBe("Bearer capability-1");
    expect(new Headers(calls[6]?.init?.headers).get("authorization")).toBe("Bearer capability-2");
    expect(new Headers(calls[1]?.init?.headers).get("content-type")).toBe("application/json");
    expect(new Headers(calls[7]?.init?.headers).has("content-type")).toBe(false);
    expect(calls[7]?.init?.body).toBe(form);
    expect(calls[7]?.init?.credentials).toBe("same-origin");
    expect(calls[9]?.init?.credentials).toBe("same-origin");
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
