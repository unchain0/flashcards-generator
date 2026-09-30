import { afterEach, describe, expect, it, vi } from "vitest";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function pageMarkup(): string {
  return `
    <main id="main-content">
      <form id="auth-form"><input id="access-password" name="password" type="password"></form>
      <button id="login-button"></button>
      <button id="logout-button"></button>
      <button id="connect-notebooklm"></button>
      <form id="generation-form">
        <fieldset id="generation-fields" disabled>
          <legend id="generation-legend"></legend>
          <input id="files" name="files" type="file" required>
          <input id="language" name="language" value="pt_BR" required>
          <select id="study-profile" name="study_profile">
            <option value="general" selected>Geração geral</option>
            <option value="english-context">Inglês em contexto</option>
          </select>
          <textarea id="instructions" name="instructions"></textarea>
          <p id="file-error" hidden></p>
          <span id="file-summary"></span>
          <button id="start-generation" disabled></button>
        </fieldset>
      </form>
      <section id="auth-panel"><p id="auth-status"></p></section>
      <section id="dashboard" hidden>
        <p id="notebook-status"></p>
        <p id="app-status"></p>
        <div id="job-empty"></div>
        <div id="job-status" hidden>
          <p id="job-message"></p>
          <progress id="job-progress"></progress>
          <p id="job-counts"></p>
          <ul id="job-files"></ul>
        </div>
      </section>
    </main>
  `;
}

describe("web entry point", () => {
  let originalUrlMethods = new Map<string, PropertyDescriptor | undefined>();

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    for (const [name, descriptor] of originalUrlMethods) {
      if (descriptor) {
        Object.defineProperty(URL, name, descriptor);
      } else {
        Reflect.deleteProperty(URL, name);
      }
    }
    originalUrlMethods = new Map();
    document.body.innerHTML = "";
  });

  it("requires the dashboard root and its required form elements", async () => {
    document.body.innerHTML = "";
    vi.resetModules();
    await expect(import("./main")).rejects.toThrow("A interface principal não foi encontrada.");

    document.body.innerHTML = "<main id='main-content'></main>";
    vi.resetModules();
    await expect(import("./main")).rejects.toThrow("Elemento obrigatório ausente: auth-form");
  });

  it("wires password login, connection, generation, download, and logout", async () => {
    document.body.innerHTML = pageMarkup();
    const calls: string[] = [];
    const submittedForms: FormData[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const path = String(input);
      calls.push(path);
      if (path.endsWith("/auth/me")) {
        return response({ detail: "Sessão ausente" }, 401);
      }
      if (path.endsWith("/auth/login")) {
        return response({ authenticated: true });
      }
      if (path.endsWith("/auth/companion/token")) {
        return response({ access_token: "capability", expires_in: 300 }, 201);
      }
      if (path.endsWith("/notebooklm/status")) {
        return response({
          authenticated: false,
          status: "login_required",
          message: "login required",
        });
      }
      if (path.endsWith("/notebooklm/login")) {
        return response({
          authenticated: true,
          status: "authenticated",
          message: "NotebookLM conectado.",
        });
      }
      if (path === "http://127.0.0.1:8765/v1/jobs" && init?.method === "POST") {
        if (init.body instanceof FormData) {
          submittedForms.push(init.body);
        }
        return response(
          {
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
          },
          202,
        );
      }
      if (path.endsWith("/jobs/job-123")) {
        return response({
          id: "job-123",
          status: "completed",
          message: "Flashcards gerados.",
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
        });
      }
      if (path.endsWith("/artifacts/lesson.csv")) {
        return new Response("Front,Back\nterm,definition", {
          headers: { "content-type": "text/csv" },
        });
      }
      if (path.endsWith("/auth/logout")) {
        return response({ authenticated: false });
      }
      if (path === "http://127.0.0.1:8765/missing-artifact.csv") {
        return response({ detail: "Arquivo ausente" }, 404);
      }
      return response({ detail: "Não encontrado" }, 404);
    };
    vi.stubGlobal("fetch", fetcher);
    const createObjectURL = vi.fn(() => "blob:lesson");
    const revokeObjectURL = vi.fn();
    originalUrlMethods = new Map([
      ["createObjectURL", Object.getOwnPropertyDescriptor(URL, "createObjectURL")],
      ["revokeObjectURL", Object.getOwnPropertyDescriptor(URL, "revokeObjectURL")],
    ]);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    vi.resetModules();
    await import("./main");

    await vi.waitFor(() => {
      expect(document.querySelector<HTMLElement>("#auth-panel")?.hidden).toBe(false);
    });
    const password = document.querySelector<HTMLInputElement>("#access-password");
    if (!password) {
      throw new Error("Campo de senha ausente");
    }
    password.type = "file";
    document
      .querySelector<HTMLFormElement>("#auth-form")
      ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(calls).not.toContain("/api/v1/auth/login");

    password.type = "password";
    password.value = "senha-de-teste-123";
    document
      .querySelector<HTMLFormElement>("#auth-form")
      ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(password.value).toBe("");
    await vi.waitFor(() => {
      expect(document.querySelector<HTMLElement>("#dashboard")?.hidden).toBe(false);
    });

    const fileInput = document.querySelector<HTMLInputElement>("#files");
    const fileError = document.querySelector<HTMLParagraphElement>("#file-error");
    const fileSummary = document.querySelector<HTMLSpanElement>("#file-summary");
    if (!fileInput || !fileError || !fileSummary) {
      throw new Error("Controles de arquivos ausentes");
    }
    document.querySelector<HTMLButtonElement>("#connect-notebooklm")?.click();
    await vi.waitFor(() => {
      expect(document.querySelector("#notebook-status")?.textContent).toBe("NotebookLM conectado.");
    });
    expect(calls).toContain("http://127.0.0.1:8765/v1/notebooklm/status");
    expect(calls).toContain("http://127.0.0.1:8765/v1/notebooklm/login");

    const invalidEvent = new Event("invalid", { cancelable: true });
    fileInput.dispatchEvent(invalidEvent);
    expect(invalidEvent.defaultPrevented).toBe(true);
    expect(fileInput.validationMessage).toBe("Selecione ao menos um arquivo PDF ou PPTX.");
    expect(fileInput.validity.customError).toBe(true);
    expect(fileInput.getAttribute("aria-invalid")).toBe("true");
    expect(fileError.hidden).toBe(false);
    expect(fileError.textContent).toBe("Selecione ao menos um arquivo PDF ou PPTX.");
    fileInput.dispatchEvent(new Event("change"));
    expect(fileInput.validity.customError).toBe(false);
    expect(fileInput.hasAttribute("aria-invalid")).toBe(false);
    expect(fileError.hidden).toBe(true);
    expect(fileSummary.textContent).toBe("Nenhum arquivo selecionado");
    expect(document.querySelector<HTMLButtonElement>("#start-generation")?.disabled).toBe(true);
    Object.defineProperty(fileInput, "files", {
      configurable: true,
      value: [new File(["material"], "material.pdf")] as unknown as FileList,
    });
    fileInput.dispatchEvent(new Event("change"));
    expect(fileSummary.textContent).toBe("material.pdf");
    expect(document.querySelector<HTMLButtonElement>("#start-generation")?.disabled).toBe(false);
    fileInput.required = false;
    fileInput.dispatchEvent(new Event("invalid"));
    expect(fileInput.validity.customError).toBe(false);

    const files = document.querySelector<HTMLUListElement>("#job-files");
    files?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    const unlinkedButton = document.createElement("button");
    files?.append(unlinkedButton);
    unlinkedButton.click();
    const unnamedButton = document.createElement("button");
    unnamedButton.dataset.artifactUrl = "/missing-artifact.csv";
    files?.append(unnamedButton);
    unnamedButton.click();
    await vi.waitFor(() => {
      expect(document.querySelector("#app-status")?.textContent).toBe("Arquivo ausente");
    });

    const generationForm = document.querySelector<HTMLFormElement>("#generation-form");
    const instructions = document.querySelector<HTMLTextAreaElement>("#instructions");
    const studyProfile = document.querySelector<HTMLSelectElement>("#study-profile");
    const language = document.querySelector<HTMLInputElement>("#language");
    if (!generationForm || !instructions || !studyProfile || !language) {
      throw new Error("Configurações da geração ausentes");
    }
    language.value = "pt_PT";
    expect(language.disabled).toBe(false);
    instructions.value = "Use linguagem simples.";
    generationForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => {
      expect(document.querySelector("#app-status")?.textContent).toBe("Geração concluída.");
    });
    expect(submittedForms).toHaveLength(1);
    expect(submittedForms[0]?.get("instructions")).toBe("Use linguagem simples.");
    expect(submittedForms[0]?.has("study_profile")).toBe(false);
    expect(submittedForms[0]?.has("single_cloze")).toBe(false);

    studyProfile.value = "english-context";
    studyProfile.dispatchEvent(new Event("change"));
    expect(language.disabled).toBe(true);
    expect(language.value).toBe("pt_PT");
    studyProfile.dispatchEvent(new Event("change"));
    expect(language.disabled).toBe(true);
    expect(language.value).toBe("pt_PT");
    instructions.value = "Sou intermediário e já conheço as palavras mais comuns.";
    generationForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => {
      expect(submittedForms).toHaveLength(2);
    });
    expect(submittedForms[1]?.get("instructions")).toContain(
      "Sou intermediário e já conheço as palavras mais comuns.",
    );
    expect(submittedForms[1]?.get("language")).toBe("en");
    expect(submittedForms[1]?.get("single_cloze")).toBe("true");
    expect(submittedForms[1]?.has("study_profile")).toBe(false);
    const generateButton = document.querySelector<HTMLButtonElement>("#start-generation");
    await vi.waitFor(() => {
      expect(generateButton?.disabled).toBe(false);
    });

    studyProfile.value = "general";
    studyProfile.dispatchEvent(new Event("change"));
    expect(language.disabled).toBe(false);
    expect(language.value).toBe("pt_PT");

    studyProfile.value = "english-context";
    studyProfile.dispatchEvent(new Event("change"));

    instructions.value = "";
    generationForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await vi.waitFor(() => {
      expect(submittedForms).toHaveLength(3);
    });
    const defaultEnglishInstructions = submittedForms[2]?.get("instructions");
    expect(typeof defaultEnglishInstructions).toBe("string");
    expect(defaultEnglishInstructions).not.toContain(
      "Sou intermediário e já conheço as palavras mais comuns.",
    );
    await vi.waitFor(() => {
      expect(generateButton?.disabled).toBe(false);
    });

    instructions.value = "x".repeat(10_000);
    generationForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    expect(document.querySelector("#app-status")?.textContent).toBe(
      "Reduza as instruções adicionais para caber junto ao perfil de inglês.",
    );
    expect(submittedForms).toHaveLength(3);
    document.querySelector<HTMLButtonElement>("#job-files button[data-artifact-url]")?.click();
    await vi.waitFor(() => {
      expect(document.querySelector("#app-status")?.textContent).toBe("Download iniciado.");
    });
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(calls).toContain("http://127.0.0.1:8765/v1/jobs");
    expect(calls).toContain("http://127.0.0.1:8765/v1/jobs/job-123");
    expect(calls).toContain("http://127.0.0.1:8765/v1/jobs/job-123/artifacts/lesson.csv");

    document.querySelector<HTMLButtonElement>("#logout-button")?.click();
    await vi.waitFor(() => {
      expect(document.querySelector<HTMLElement>("#dashboard")?.hidden).toBe(true);
    });
    expect(calls).toContain("/api/v1/auth/logout");
    expect(revokeObjectURL).not.toHaveBeenCalled();
  });
});
