import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GenerationJob } from "../domain/contracts";
import { BrowserDashboardView } from "./browser_view";

function makeJob(overrides: Partial<GenerationJob> = {}): GenerationJob {
  return {
    id: "job-123",
    status: "running",
    message: "Em andamento",
    filenames: ["lesson.pdf"],
    discovered_sources: 2,
    completed_sources: 1,
    skipped_sources: 0,
    failed_sources: 0,
    artifacts: [],
    error: null,
    ...overrides,
  };
}

function createView(): BrowserDashboardView {
  document.body.innerHTML = `
    <main>
      <section id="auth-panel"></section>
      <section id="dashboard"></section>
      <button id="logout-button"></button>
      <button id="login-button"></button>
      <button id="connect-notebooklm"></button>
      <fieldset id="generation-fields" disabled>
        <legend id="generation-legend"></legend>
        <button id="start-generation"></button>
      </fieldset>
      <p id="auth-status"></p>
      <p id="app-status"></p>
      <p id="notebook-status"></p>
      <div id="job-empty"></div>
      <div id="job-status"></div>
      <p id="job-message"></p>
      <progress id="job-progress"></progress>
      <p id="job-counts"></p>
      <ul id="job-files"></ul>
    </main>
  `;
  const root = document.querySelector<HTMLElement>("main");
  if (!root) {
    throw new Error("Raiz de teste ausente");
  }
  return new BrowserDashboardView(root);
}

describe("BrowserDashboardView", () => {
  beforeEach(() => {
    createView();
  });

  it("toggles the main panels and writes live status messages", () => {
    const root = document.querySelector<HTMLElement>("main");
    if (!root) {
      throw new Error("Raiz de teste ausente");
    }
    const view = new BrowserDashboardView(root);

    view.showAuthentication(false);
    view.showDashboard(true);
    view.showLogout(true);
    view.setAuthenticationMessage("Senha inválida.");
    view.setApplicationMessage("Pronto.");
    view.setNotebookMessage("NotebookLM conectado.", "connected");

    expect(document.querySelector<HTMLElement>("#auth-panel")?.hidden).toBe(true);
    expect(document.querySelector<HTMLElement>("#dashboard")?.hidden).toBe(false);
    expect(document.querySelector<HTMLButtonElement>("#logout-button")?.hidden).toBe(false);
    expect(document.querySelector("#auth-status")?.textContent).toBe("Senha inválida.");
    expect(document.querySelector("#app-status")?.textContent).toBe("Pronto.");
    expect(document.querySelector<HTMLElement>("#notebook-status")?.dataset.state).toBe(
      "connected",
    );

    view.showAuthentication(true);
    view.showDashboard(false);
    view.showLogout(false);
    view.setNotebookMessage("Conecte sua conta.", "disconnected");

    expect(document.querySelector<HTMLElement>("#auth-panel")?.hidden).toBe(false);
    expect(document.querySelector<HTMLElement>("#dashboard")?.hidden).toBe(true);
    expect(document.querySelector<HTMLButtonElement>("#logout-button")?.hidden).toBe(true);
    expect(document.querySelector<HTMLElement>("#notebook-status")?.dataset.state).toBe(
      "disconnected",
    );

    view.setNotebookMessage("Auxiliar indisponível.", "error");
    expect(document.querySelector<HTMLElement>("#notebook-status")?.dataset.state).toBe("error");
  });

  it("sets busy state on each action button", () => {
    const view = createView();

    for (const action of ["login", "logout", "connect", "generate"] as const) {
      view.setBusy(action, true);
      const buttonId = {
        login: "login-button",
        logout: "logout-button",
        connect: "connect-notebooklm",
        generate: "start-generation",
      }[action];
      expect(document.querySelector<HTMLButtonElement>(`#${buttonId}`)?.disabled).toBe(true);
      view.setBusy(action, false);
    }
  });

  it("requires selected files and an idle generation before enabling submit", () => {
    const view = createView();
    const button = document.querySelector<HTMLButtonElement>("#start-generation");

    view.setGenerationFilesSelected(false);
    expect(button?.disabled).toBe(true);
    view.setGenerationFilesSelected(true);
    expect(button?.disabled).toBe(false);
    view.setBusy("generate", true);
    expect(button?.disabled).toBe(true);
    view.setGenerationFilesSelected(false);
    view.setBusy("generate", false);
    expect(button?.disabled).toBe(true);
    view.setGenerationFilesSelected(true);
    expect(button?.disabled).toBe(false);
  });

  it("enables generation only after the local profile is authenticated", () => {
    const view = createView();
    const fields = document.querySelector<HTMLFieldSetElement>("#generation-fields");

    view.setGenerationEnabled(true);
    expect(fields?.disabled).toBe(false);
    expect(document.querySelector("#generation-legend")?.textContent).toBe(
      "Escolha os arquivos para configurar a geração.",
    );
    view.setGenerationEnabled(false);
    expect(fields?.disabled).toBe(true);
    expect(document.querySelector("#generation-legend")?.textContent).toBe(
      "Conecte o NotebookLM neste computador para habilitar a geração.",
    );
  });

  it("renders progress, errors, artifacts, and empty completion states", () => {
    const view = createView();
    view.renderJob(
      makeJob({
        completed_sources: 2,
        skipped_sources: 1,
        failed_sources: 1,
        artifacts: [{ name: "lesson.csv", url: "/lesson.csv" }],
        error: "Um arquivo foi ignorado.",
      }),
    );

    const progress = document.querySelector<HTMLProgressElement>("#job-progress");
    const status = document.querySelector<HTMLElement>("#job-status");
    expect(progress?.max).toBe(2);
    expect(progress?.value).toBe(2);
    expect(status?.dataset.status).toBe("running");
    expect(document.querySelector("#job-counts")?.textContent).toBe(
      "4 de 2 arquivo(s) processado(s).",
    );
    expect(document.querySelector("#job-message")?.textContent).toBe(
      "Em andamento Um arquivo foi ignorado.",
    );
    expect(document.querySelector<HTMLButtonElement>("#job-files button")?.dataset).toMatchObject({
      artifactUrl: "/lesson.csv",
      artifactName: "lesson.csv",
    });
    expect(document.querySelector<HTMLElement>("#job-empty")?.hidden).toBe(true);

    view.renderJob(makeJob({ discovered_sources: 0, artifacts: [] }));
    expect(progress?.hasAttribute("value")).toBe(false);
    expect(document.querySelector("#job-counts")?.textContent).toBe("Preparando os arquivos...");
    expect(document.querySelector("#job-files li")).toBeNull();

    view.renderJob(makeJob({ status: "completed", discovered_sources: 1, artifacts: [] }));
    expect(document.querySelector("#job-files li")?.textContent).toBe(
      "A geração terminou sem CSV novo.",
    );

    view.renderJob(makeJob({ status: "failed", error: "NotebookLM indisponível." }));
    expect(status?.dataset.status).toBe("failed");
  });

  it("saves a CSV through a temporary browser download URL", async () => {
    const view = createView();
    const createObjectURL = vi.fn(() => "blob:flashcards");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.useFakeTimers();
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    view.saveArtifact(new Blob(["Front,Back"]), "lesson.csv");

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(document.querySelector("a[download='lesson.csv']")).toBeNull();
    await vi.advanceTimersByTimeAsync(1000);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:flashcards");
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("fails clearly when required page elements are missing", () => {
    document.body.innerHTML = "<main></main>";
    const root = document.querySelector<HTMLElement>("main");
    if (!root) {
      throw new Error("Raiz de teste ausente");
    }
    const view = new BrowserDashboardView(root);

    expect(() => view.setAuthenticationMessage("mensagem")).toThrow(
      "Elemento obrigatório ausente: auth-status",
    );
  });
});
