import {
  HttpApiError,
  type DashboardView,
  type FlashcardsApi,
  type NotebookLMStatus,
} from "../domain/contracts";

type Wait = (milliseconds: number) => Promise<void>;

const wait: Wait = (milliseconds) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const TERMINAL_JOB_STATES = new Set(["completed", "failed", "cancelled"]);
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
      this.view.setGenerationEnabled(false);
    } finally {
      this.view.setBusy("connect", false);
    }
  }

  async generate(form: FormData): Promise<void> {
    this.view.setBusy("generate", true);
    this.view.setApplicationMessage("Enviando arquivos...");
    try {
      const job = await this.api.createJob(form);
      this.activeJobId = job.id;
      this.view.renderJob(job);
      await this.pollJob(job.id);
    } catch (error) {
      this.view.setBusy("generate", false);
      this.view.setApplicationMessage(this.message(error));
    }
  }

  async downloadArtifact(url: string, name: string): Promise<void> {
    try {
      this.view.saveArtifact(await this.api.download(url), name);
      this.view.setApplicationMessage("Download iniciado.");
    } catch (error) {
      this.view.setApplicationMessage(this.message(error));
    }
  }

  private async enterDashboard(): Promise<void> {
    this.view.showAuthentication(false);
    this.view.showDashboard(true);
    this.view.showLogout(true);
    this.view.setApplicationMessage("");
    this.view.setGenerationEnabled(false);
    this.view.setNotebookMessage(
      "Clique em Conectar para verificar sua sessão local do NotebookLM.",
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
    this.view.setGenerationEnabled(status.authenticated);
  }

  private async pollJob(jobId: string): Promise<void> {
    while (this.activeJobId === jobId) {
      const job = await this.api.getJob(jobId);
      if (this.activeJobId !== jobId) {
        return;
      }
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
  }

  private message(error: unknown): string {
    return error instanceof Error ? error.message : "Ocorreu um erro inesperado.";
  }
}
