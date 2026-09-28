import { describe, expect, it, vi } from "vitest";
import type {
  DashboardView,
  FlashcardsApi,
  GenerationJob,
  JobStatus,
  NotebookLMStatus,
} from "../domain/contracts";
import { HttpApiError } from "../infrastructure/http_api";
import { DashboardController } from "./dashboard_controller";

function notebookStatus(status: string): NotebookLMStatus {
  return {
    authenticated: status === "authenticated",
    status,
    message: status,
  };
}

function job(status: JobStatus, message: string = status): GenerationJob {
  return {
    id: "job-123",
    status,
    message,
    filenames: ["lesson.pdf"],
    discovered_sources: 1,
    completed_sources: status === "completed" ? 1 : 0,
    skipped_sources: 0,
    failed_sources: status === "failed" ? 1 : 0,
    artifacts: [],
    error: null,
  };
}

function harness() {
  const api = {
    me: vi.fn<FlashcardsApi["me"]>().mockResolvedValue({ authenticated: true }),
    login: vi.fn<FlashcardsApi["login"]>().mockResolvedValue({ authenticated: true }),
    logout: vi.fn<FlashcardsApi["logout"]>().mockResolvedValue({ authenticated: false }),
    notebookStatus: vi
      .fn<FlashcardsApi["notebookStatus"]>()
      .mockResolvedValue(notebookStatus("login_required")),
    startNotebookLogin: vi
      .fn<FlashcardsApi["startNotebookLogin"]>()
      .mockResolvedValue(notebookStatus("login_started")),
    createJob: vi.fn<FlashcardsApi["createJob"]>().mockResolvedValue(job("queued")),
    getJob: vi.fn<FlashcardsApi["getJob"]>().mockResolvedValue(job("completed")),
    download: vi.fn<FlashcardsApi["download"]>().mockResolvedValue(new Blob(["csv"])),
  } satisfies FlashcardsApi;
  const view: DashboardView = {
    showAuthentication: vi.fn(),
    showDashboard: vi.fn(),
    showLogout: vi.fn(),
    setGenerationEnabled: vi.fn(),
    setGenerationFilesSelected: vi.fn(),
    setAuthenticationMessage: vi.fn(),
    setApplicationMessage: vi.fn(),
    setNotebookMessage: vi.fn(),
    setBusy: vi.fn(),
    renderJob: vi.fn(),
    saveArtifact: vi.fn(),
  };
  const pause = vi.fn<(milliseconds: number) => Promise<void>>().mockResolvedValue(undefined);
  const controller = new DashboardController(api, view, pause);
  return { api, controller, pause, view };
}

describe("DashboardController", () => {
  it("opens the dashboard without making an unsolicited local network request", async () => {
    const { api, controller, view } = harness();

    await controller.initialize();

    expect(api.me).toHaveBeenCalledOnce();
    expect(view.showAuthentication).toHaveBeenCalledWith(false);
    expect(view.showDashboard).toHaveBeenCalledWith(true);
    expect(view.showLogout).toHaveBeenCalledWith(true);
    expect(api.notebookStatus).not.toHaveBeenCalled();
    expect(view.setNotebookMessage).toHaveBeenCalledWith(
      "Clique em Conectar para verificar sua sessão local do NotebookLM.",
      "disconnected",
    );
    expect(view.setGenerationEnabled).toHaveBeenCalledWith(false);
    expect(view.setApplicationMessage).toHaveBeenCalledWith("");
  });

  it("shows login for an expired session and reports other initialization errors", async () => {
    const unauthorized = harness();
    unauthorized.api.me.mockRejectedValueOnce(new HttpApiError("expirou", 401));
    await unauthorized.controller.initialize();
    expect(unauthorized.view.showAuthentication).toHaveBeenCalledWith(true);
    expect(unauthorized.view.setAuthenticationMessage).not.toHaveBeenCalled();

    const unavailable = harness();
    unavailable.api.me.mockRejectedValueOnce(new Error("offline"));
    await unavailable.controller.initialize();
    expect(unavailable.view.setAuthenticationMessage).toHaveBeenCalledWith("offline");
  });

  it("handles successful login, invalid passwords, and unexpected login errors", async () => {
    const { api, controller, view } = harness();

    api.login.mockRejectedValueOnce(new HttpApiError("inválida", 401));
    await controller.login("errada");
    expect(view.setAuthenticationMessage).toHaveBeenLastCalledWith(
      "Senha inválida. Confira e tente novamente.",
    );

    api.login.mockRejectedValueOnce("erro desconhecido");
    await controller.login("incerta");
    expect(view.setAuthenticationMessage).toHaveBeenLastCalledWith("Ocorreu um erro inesperado.");

    await controller.login("senha-correta");
    expect(api.login).toHaveBeenLastCalledWith("senha-correta");
    expect(view.showDashboard).toHaveBeenLastCalledWith(true);
    expect(view.setBusy).toHaveBeenLastCalledWith("login", false);
  });

  it("closes a session and reports logout errors", async () => {
    const { api, controller, view } = harness();

    await controller.logout();
    expect(api.logout).toHaveBeenCalledOnce();
    expect(view.showAuthentication).toHaveBeenLastCalledWith(true);
    expect(view.setAuthenticationMessage).toHaveBeenLastCalledWith("Acesso encerrado.");

    api.logout.mockRejectedValueOnce(new Error("rede indisponível"));
    await controller.logout();
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("rede indisponível");
    expect(view.setBusy).toHaveBeenLastCalledWith("logout", false);
  });

  it("stops rendering an old job after logout changes the active job", async () => {
    const { api, controller, view } = harness();
    api.getJob.mockImplementationOnce(async () => {
      await controller.logout();
      return job("running", "Em andamento");
    });

    await controller.generate(new FormData());

    expect(view.showAuthentication).toHaveBeenLastCalledWith(true);
    expect(view.setBusy).toHaveBeenCalledWith("generate", false);
    expect(api.getJob).toHaveBeenCalledOnce();
  });

  it("reports companion errors without trying login when status cannot be checked", async () => {
    const { api, controller, pause, view } = harness();

    api.notebookStatus.mockRejectedValueOnce(new Error("serviço offline"));
    await controller.connectNotebookLM();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith("serviço offline", "error");
    expect(view.setGenerationEnabled).toHaveBeenLastCalledWith(false);
    expect(api.startNotebookLogin).not.toHaveBeenCalled();

    api.notebookStatus.mockResolvedValueOnce(notebookStatus("login_required"));
    api.startNotebookLogin.mockRejectedValueOnce(
      new HttpApiError("O auxiliar local do NotebookLM ainda não está disponível.", 503),
    );
    await controller.connectNotebookLM();
    expect(pause).not.toHaveBeenCalled();
    expect(api.notebookStatus).toHaveBeenCalledTimes(2);
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith(
      "O auxiliar local do NotebookLM ainda não está disponível.",
      "error",
    );

    api.notebookStatus.mockResolvedValueOnce(notebookStatus("login_required"));
    api.startNotebookLogin.mockRejectedValueOnce("sem conexão");
    await controller.connectNotebookLM();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith(
      "Ocorreu um erro inesperado.",
      "error",
    );
  });

  it("checks the local profile and skips login when it is already authenticated", async () => {
    const { api, view } = harness();
    api.notebookStatus.mockResolvedValueOnce(notebookStatus("authenticated"));
    const controller = new DashboardController(api, view);

    await controller.connectNotebookLM();

    expect(api.notebookStatus).toHaveBeenCalledOnce();
    expect(api.startNotebookLogin).not.toHaveBeenCalled();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith("NotebookLM conectado.", "connected");
    expect(view.setGenerationEnabled).toHaveBeenLastCalledWith(true);
  });

  it("uses the browser timer while polling a running generation", async () => {
    const { api, view } = harness();
    api.getJob
      .mockResolvedValueOnce(job("running", "Em andamento"))
      .mockResolvedValueOnce(job("completed", "Flashcards gerados."));
    const controller = new DashboardController(api, view);
    vi.useFakeTimers();

    try {
      const generation = controller.generate(new FormData());
      await Promise.resolve();
      await Promise.resolve();
      expect(vi.getTimerCount()).toBe(1);
      await vi.advanceTimersByTimeAsync(1200);
      await generation;
    } finally {
      vi.useRealTimers();
    }

    expect(api.getJob).toHaveBeenCalledTimes(2);
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("Geração concluída.");
  });

  it("localizes known NotebookLM states and preserves unknown provider details", async () => {
    const { api, controller, view } = harness();
    api.notebookStatus.mockResolvedValueOnce(notebookStatus("login_required"));
    api.startNotebookLogin.mockResolvedValueOnce(notebookStatus("login_required"));
    await controller.connectNotebookLM();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith(
      "Nenhuma conta do NotebookLM conectada.",
      "disconnected",
    );

    api.notebookStatus.mockResolvedValueOnce(notebookStatus("login_required"));
    api.startNotebookLogin.mockResolvedValueOnce(notebookStatus("provider_error"));
    await controller.connectNotebookLM();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith("provider_error", "disconnected");
    expect(view.setGenerationEnabled).toHaveBeenLastCalledWith(false);
  });

  it("handles a completed generation and a failed job", async () => {
    const { api, controller, pause, view } = harness();
    api.getJob
      .mockResolvedValueOnce(job("running", "Em andamento"))
      .mockResolvedValueOnce(job("completed", "Flashcards gerados."));
    await controller.generate(new FormData());
    expect(api.createJob).toHaveBeenCalledOnce();
    expect(api.getJob).toHaveBeenCalledTimes(2);
    expect(pause).toHaveBeenCalledOnce();
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("Geração concluída.");
    expect(view.setBusy).toHaveBeenLastCalledWith("generate", false);

    api.createJob.mockResolvedValueOnce(job("queued"));
    api.getJob.mockResolvedValueOnce(job("failed", "A geração falhou."));
    await controller.generate(new FormData());
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("A geração falhou.");
  });

  it("reports generation errors and keeps long jobs available for later polling", async () => {
    const failed = harness();
    failed.api.createJob.mockRejectedValueOnce(new Error("upload falhou"));
    await failed.controller.generate(new FormData());
    expect(failed.view.setApplicationMessage).toHaveBeenLastCalledWith("upload falhou");
    expect(failed.view.setBusy).toHaveBeenLastCalledWith("generate", false);

    const longJob = harness();
    longJob.api.getJob.mockResolvedValue(job("running", "Em andamento"));
    await longJob.controller.generate(new FormData());
    expect(longJob.api.getJob).toHaveBeenCalledTimes(240);
    expect(longJob.pause).toHaveBeenCalledTimes(240);
    expect(longJob.view.setApplicationMessage).toHaveBeenLastCalledWith(
      expect.stringContaining("A geração continua no servidor"),
    );
  });

  it("downloads artifacts and reports download errors", async () => {
    const { api, controller, view } = harness();

    await controller.downloadArtifact("/file.csv", "file.csv");
    expect(api.download).toHaveBeenCalledWith("/file.csv");
    expect(view.saveArtifact).toHaveBeenCalledWith(expect.any(Blob), "file.csv");
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("Download iniciado.");

    api.download.mockRejectedValueOnce(new Error("arquivo indisponível"));
    await controller.downloadArtifact("/file.csv", "file.csv");
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("arquivo indisponível");
  });
});
