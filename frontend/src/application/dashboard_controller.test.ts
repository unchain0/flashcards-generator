import { describe, expect, it, vi } from "vite-plus/test";
import {
  HttpApiError,
  type DashboardView,
  type FlashcardsApi,
  type NotebookLMStatus,
  type ServerJobStatus,
} from "../domain/contracts";
import { DashboardController } from "./dashboard_controller";

function notebookStatus(status: string): NotebookLMStatus {
  return {
    authenticated: status === "authenticated",
    status,
    message: status,
  };
}

function serverStatus(
  status: string,
  cards = 0,
  downloadUrl = "/api/v1/jobs/job-123/csv",
): ServerJobStatus {
  return { status, cards, download_url: downloadUrl };
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
    createServerJob: vi
      .fn<FlashcardsApi["createServerJob"]>()
      .mockResolvedValue({ job_id: "job-123" }),
    getServerJob: vi.fn<FlashcardsApi["getServerJob"]>().mockResolvedValue(serverStatus("done", 5)),
    downloadServer: vi.fn<FlashcardsApi["downloadServer"]>().mockResolvedValue(new Blob(["csv"])),
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

function pdfForm(): FormData {
  const form = new FormData();
  form.append("files", new Blob(["pdf"]), "lesson.pdf");
  return form;
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
      "Opcional: conecte o NotebookLM para gerar localmente.",
      "disconnected",
    );
    expect(view.setGenerationEnabled).toHaveBeenCalledWith(true);
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
    api.getServerJob.mockImplementationOnce(async () => {
      await controller.logout();
      return serverStatus("running");
    });

    await controller.generate(pdfForm());

    expect(view.showAuthentication).toHaveBeenLastCalledWith(true);
    expect(view.setBusy).toHaveBeenCalledWith("generate", false);
    expect(api.getServerJob).toHaveBeenCalledOnce();
  });

  it("reports companion errors without trying login when status cannot be checked", async () => {
    const { api, controller, pause, view } = harness();

    api.notebookStatus.mockRejectedValueOnce(new Error("serviço offline"));
    await controller.connectNotebookLM();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith("serviço offline", "error");
    expect(view.setGenerationEnabled).toHaveBeenLastCalledWith(true);
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

  it("stops polling when the user logs out between status requests", async () => {
    const { api, controller, pause, view } = harness();
    api.getServerJob.mockResolvedValue(serverStatus("running"));
    pause.mockImplementation(() => controller.logout());

    await controller.generate(pdfForm());

    expect(api.getServerJob).toHaveBeenCalledOnce();
    expect(pause).toHaveBeenCalledOnce();
    expect(view.showAuthentication).toHaveBeenLastCalledWith(true);
    expect(view.setBusy).toHaveBeenCalledWith("generate", false);
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

  it("rechecks provider failures without starting another Google login", async () => {
    const { api, controller, view } = harness();
    api.notebookStatus.mockResolvedValue(notebookStatus("provider_error"));

    await controller.connectNotebookLM();
    await controller.connectNotebookLM();

    expect(api.notebookStatus).toHaveBeenCalledTimes(2);
    expect(api.startNotebookLogin).not.toHaveBeenCalled();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith(
      "Não foi possível verificar sua sessão do NotebookLM. Tente conectar novamente.",
      "error",
    );
    expect(view.setGenerationEnabled).toHaveBeenLastCalledWith(true);
    expect(view.setBusy).toHaveBeenLastCalledWith("connect", false);
  });

  it("preserves unrecognized session states without starting login", async () => {
    const { api, controller, view } = harness();
    api.notebookStatus.mockResolvedValueOnce(notebookStatus("provider_pending"));

    await controller.connectNotebookLM();

    expect(api.startNotebookLogin).not.toHaveBeenCalled();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith("provider_pending", "disconnected");
    expect(view.setGenerationEnabled).toHaveBeenLastCalledWith(true);
  });

  it("uses the browser timer while polling a running generation", async () => {
    const { api, view } = harness();
    api.getServerJob
      .mockResolvedValueOnce(serverStatus("running"))
      .mockResolvedValueOnce(serverStatus("done", 5));
    const controller = new DashboardController(api, view);
    vi.useFakeTimers();

    try {
      const generation = controller.generate(pdfForm());
      await Promise.resolve();
      await Promise.resolve();
      expect(vi.getTimerCount()).toBe(1);
      await vi.advanceTimersByTimeAsync(1200);
      await generation;
    } finally {
      vi.useRealTimers();
    }

    expect(api.getServerJob).toHaveBeenCalledTimes(2);
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
    api.startNotebookLogin.mockResolvedValueOnce(notebookStatus("provider_pending"));
    await controller.connectNotebookLM();
    expect(view.setNotebookMessage).toHaveBeenLastCalledWith("provider_pending", "disconnected");
    expect(view.setGenerationEnabled).toHaveBeenLastCalledWith(true);
  });

  it("uploads the selected file, counts the cards and exposes the CSV link", async () => {
    const { api, controller, view } = harness();

    await controller.generate(pdfForm());

    expect(api.createServerJob).toHaveBeenCalledOnce();
    const upload = api.createServerJob.mock.calls[0]?.[0];
    expect(upload?.get("file")).toBeInstanceOf(File);
    expect(upload?.getAll("files")).toEqual([]);
    expect(view.renderJob).toHaveBeenCalledWith(
      expect.objectContaining({ status: "running", message: "Gerando cartões..." }),
    );
    expect(view.renderJob).toHaveBeenLastCalledWith(
      expect.objectContaining({
        status: "completed",
        message: "5 cartão(ões) gerado(s).",
        artifacts: [{ name: "flashcards.csv", url: "/api/v1/jobs/job-123/csv" }],
      }),
    );
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("Geração concluída.");
    expect(view.setBusy).toHaveBeenLastCalledWith("generate", false);
  });

  it("keeps the form as-is when no file is selected and reports a failed job", async () => {
    const { api, controller, view } = harness();
    const empty = new FormData();

    await controller.generate(empty);
    expect(api.createServerJob).toHaveBeenCalledWith(empty);

    api.getServerJob.mockResolvedValueOnce(serverStatus("failed"));
    await controller.generate(pdfForm());
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("A geração falhou.");
  });

  it("reports generation errors and gives up on a job that never finishes", async () => {
    const failed = harness();
    failed.api.createServerJob.mockRejectedValueOnce(new Error("upload falhou"));
    await failed.controller.generate(pdfForm());
    expect(failed.view.setApplicationMessage).toHaveBeenLastCalledWith("upload falhou");
    expect(failed.view.setBusy).toHaveBeenLastCalledWith("generate", false);

    const longJob = harness();
    longJob.api.getServerJob.mockResolvedValue(serverStatus("running"));
    await longJob.controller.generate(pdfForm());
    expect(longJob.api.getServerJob).toHaveBeenCalledTimes(120);
    expect(longJob.pause).toHaveBeenCalledTimes(120);
    expect(longJob.view.setApplicationMessage).toHaveBeenLastCalledWith(
      "A geração demorou mais que o esperado. Tente novamente.",
    );
  });

  it("downloads the CSV from the server and reports download errors", async () => {
    const { api, controller, view } = harness();

    await controller.downloadArtifact("/api/v1/jobs/job-123/csv", "flashcards.csv");
    expect(api.downloadServer).toHaveBeenCalledWith("/api/v1/jobs/job-123/csv");
    expect(view.saveArtifact).toHaveBeenCalledWith(expect.any(Blob), "flashcards.csv");
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("Download iniciado.");

    api.downloadServer.mockRejectedValueOnce(new Error("arquivo indisponível"));
    await controller.downloadArtifact("/api/v1/jobs/job-123/csv", "flashcards.csv");
    expect(view.setApplicationMessage).toHaveBeenLastCalledWith("arquivo indisponível");
  });
});
