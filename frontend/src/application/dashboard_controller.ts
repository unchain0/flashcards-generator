import {
  HttpApiError,
  type DashboardView,
  type FlashcardsApi,
  type GenerationJob,
  type NotebookLMStatus,
  type ServerJobStatus,
} from "../domain/contracts";

type Wait = (milliseconds: number) => Promise<void>;

const wait: Wait = (milliseconds) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const TERMINAL_JOB_STATES = new Set(["completed", "failed", "cancelled"]);
const SERVER_DONE_STATES = new Set(["done", "completed"]);
const SERVER_POLL_LIMIT = 120;
const NOTEBOOK_STATUS_MESSAGES: Readonly<Record<string, string>> = {
  authenticated: "NotebookLM conectado.",
  login_required: "Nenhuma conta do NotebookLM conectada.",
  provider_error: "Não foi possível verificar sua sessão do NotebookLM. Tente conectar novamente.",
};

export class DashboardController {
  private activeJobId: string | null = null;

  constructor(
    private readonly api: FlashcardsApi,
    private readonly view: DashboardView,
    private readonly pause: Wait = wait,
  ) {}

  async initialize(): Promise<void> {
    try {
      await this.api.me();
      await this.enterDashboard();
    } catch (error) {
      if (error instanceof HttpApiError && error.status === 401) {
        this.showLogin();
        return;
      }
      this.showLogin();
      this.view.setAuthenticationMessage(this.message(error));
    }
  }

  async login(password: string): Promise<void> {
    this.view.setBusy("login", true);
    this.view.setAuthenticationMessage("Validando a senha...");
    try {
      await this.api.login(password);
      await this.enterDashboard();
    } catch (error) {
      this.view.setAuthenticationMessage(
        error instanceof HttpApiError && error.status === 401
          ? "Senha inválida. Confira e tente novamente."
          : this.message(error),
      );
    } finally {
      this.view.setBusy("login", false);
    }
  }

  async logout(): Promise<void> {
    this.view.setBusy("logout", true);
    try {
      await this.api.logout();
      this.activeJobId = null;
      this.view.setBusy("generate", false);
      this.showLogin();
      this.view.setAuthenticationMessage("Acesso encerrado.");
    } catch (error) {
      this.view.setApplicationMessage(this.message(error));
    } finally {
      this.view.setBusy("logout", false);
    }
  }

  async connectNotebookLM(): Promise<void> {
    this.view.setBusy("connect", true);
    try {
      const status = await this.api.notebookStatus();
      this.displayNotebookStatus(
        !status.authenticated && status.status === "login_required"
          ? await this.api.startNotebookLogin()
          : status,
      );
    } catch (error) {
      this.view.setNotebookMessage(this.message(error), "error");
      this.view.setGenerationEnabled(true);
    } finally {
      this.view.setBusy("connect", false);
    }
  }

  async generate(form: FormData): Promise<void> {
    this.view.setBusy("generate", true);
    this.view.setApplicationMessage("Enviando arquivos...");
    try {
      const created = await this.api.createServerJob(this.uploadForm(form));
      this.activeJobId = created.job_id;
      this.view.renderJob(this.pendingJob(created.job_id));
      await this.pollServerJob(created.job_id);
    } catch (error) {
      this.view.setBusy("generate", false);
      this.view.setApplicationMessage(this.message(error));
    }
  }

  async downloadArtifact(url: string, name: string): Promise<void> {
    try {
      this.view.saveArtifact(await this.api.downloadServer(url), name);
      this.view.setApplicationMessage("Download iniciado.");
    } catch (error) {
      this.view.setApplicationMessage(this.message(error));
    }
  }

  /// The v1 endpoint takes one PDF per job, so the panel sends the first file
  /// plus the form options the server ignores today and the LLM path will use.
  private uploadForm(form: FormData): FormData {
    const upload = new FormData();
    for (const [key, value] of form.entries()) {
      if (key !== "files") {
        upload.append(key, value);
      }
    }
    const [file] = form.getAll("files").filter((value): value is File => value instanceof File);
    if (file) {
      upload.set("file", file);
    }
    return upload;
  }

  private pendingJob(jobId: string): GenerationJob {
    return {
      id: jobId,
      status: "running",
      message: "Gerando cartões...",
      filenames: [],
      discovered_sources: 0,
      completed_sources: 0,
      skipped_sources: 0,
      failed_sources: 0,
      artifacts: [],
      error: null,
    };
  }

  private serverJob(jobId: string, status: ServerJobStatus): GenerationJob {
    const done = SERVER_DONE_STATES.has(status.status);
    const failed = status.status === "failed";
    return {
      id: jobId,
      status: failed ? "failed" : done ? "completed" : "running",
      message: done
        ? `${status.cards} cartão(ões) gerado(s).`
        : failed
          ? "A geração falhou."
          : "Gerando cartões...",
      filenames: [],
      discovered_sources: 0,
      completed_sources: 0,
      skipped_sources: 0,
      failed_sources: 0,
      artifacts: done ? [{ name: "flashcards.csv", url: status.download_url }] : [],
      error: null,
    };
  }

  private async enterDashboard(): Promise<void> {
    this.view.showAuthentication(false);
    this.view.showDashboard(true);
    this.view.showLogout(true);
    this.view.setApplicationMessage("");
    this.view.setGenerationEnabled(true);
    this.view.setNotebookMessage(
      "Opcional: conecte o NotebookLM para gerar localmente.",
      "disconnected",
    );
  }

  private showLogin(): void {
    this.view.setGenerationEnabled(false);
    this.view.showAuthentication(true);
    this.view.showDashboard(false);
    this.view.showLogout(false);
  }

  private displayNotebookStatus(status: NotebookLMStatus): void {
    const message = NOTEBOOK_STATUS_MESSAGES[status.status] ?? status.message;
    const state = status.authenticated
      ? "connected"
      : status.status === "provider_error"
        ? "error"
        : "disconnected";
    this.view.setNotebookMessage(message, state);
    this.view.setGenerationEnabled(true);
  }

  private async pollServerJob(jobId: string): Promise<void> {
    for (let attempt = 0; attempt < SERVER_POLL_LIMIT; attempt += 1) {
      if (this.activeJobId !== jobId) {
        return;
      }
      const status = await this.api.getServerJob(jobId);
      if (this.activeJobId !== jobId) {
        return;
      }
      const job = this.serverJob(jobId, status);
      this.view.renderJob(job);
      if (TERMINAL_JOB_STATES.has(job.status)) {
        this.view.setBusy("generate", false);
        this.view.setApplicationMessage(
          job.status === "completed" ? "Geração concluída." : job.message,
        );
        return;
      }
      await this.pause(1200);
    }
    this.view.setBusy("generate", false);
    this.view.setApplicationMessage("A geração demorou mais que o esperado. Tente novamente.");
  }

  private message(error: unknown): string {
    return error instanceof Error ? error.message : "Ocorreu um erro inesperado.";
  }
}
