import type { DashboardView, GenerationJob, NotebookConnectionState } from "../domain/contracts";

export class BrowserDashboardView implements DashboardView {
  private generationBusy = false;
  private generationFilesSelected = false;

  constructor(private readonly root: HTMLElement) {}

  showAuthentication(visible: boolean): void {
    this.element<HTMLElement>("auth-panel").hidden = !visible;
  }

  showDashboard(visible: boolean): void {
    this.element<HTMLElement>("dashboard").hidden = !visible;
  }

  showLogout(visible: boolean): void {
    this.element<HTMLButtonElement>("logout-button").hidden = !visible;
  }

  setAuthenticationMessage(message: string): void {
    this.text("auth-status", message);
  }

  setApplicationMessage(message: string): void {
    this.text("app-status", message);
  }

  setNotebookMessage(message: string, state: NotebookConnectionState): void {
    const status = this.element<HTMLElement>("notebook-status");
    status.textContent = message;
    status.dataset.state = state;
  }

  setGenerationEnabled(enabled: boolean): void {
    this.element<HTMLFieldSetElement>("generation-fields").disabled = !enabled;
    this.text(
      "generation-legend",
      enabled
        ? "Escolha os arquivos para configurar a geração."
        : "Conecte o NotebookLM neste computador para habilitar a geração.",
    );
  }

  setGenerationFilesSelected(selected: boolean): void {
    this.generationFilesSelected = selected;
    this.syncGenerationButton();
  }

  setBusy(action: "login" | "logout" | "connect" | "generate", busy: boolean): void {
    if (action === "generate") {
      this.generationBusy = busy;
      this.syncGenerationButton();
      return;
    }
    const ids = {
      login: "login-button",
      logout: "logout-button",
      connect: "connect-notebooklm",
    };
    this.element<HTMLButtonElement>(ids[action]).disabled = busy;
  }

  renderJob(job: GenerationJob): void {
    this.element<HTMLElement>("job-empty").hidden = true;
    const status = this.element<HTMLElement>("job-status");
    status.hidden = false;
    status.dataset.status = job.status;
    this.text("job-message", job.error ? `${job.message} ${job.error}` : job.message);
    this.renderProgress(job);
    this.renderArtifacts(job);
  }

  saveArtifact(blob: Blob, name: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private renderProgress(job: GenerationJob): void {
    const progress = this.element<HTMLProgressElement>("job-progress");
    const processed = job.completed_sources + job.skipped_sources + job.failed_sources;
    if (job.discovered_sources > 0) {
      progress.max = job.discovered_sources;
      progress.value = Math.min(processed, job.discovered_sources);
      this.text(
        "job-counts",
        `${processed} de ${job.discovered_sources} arquivo(s) processado(s).`,
      );
      return;
    }
    progress.removeAttribute("value");
    this.text("job-counts", "Preparando os arquivos...");
  }

  private renderArtifacts(job: GenerationJob): void {
    const list = this.element<HTMLUListElement>("job-files");
    list.replaceChildren();
    if (!job.artifacts.length && job.status === "completed") {
      const empty = document.createElement("li");
      empty.textContent = "A geração terminou sem CSV novo.";
      list.append(empty);
    }
    for (const artifact of job.artifacts) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `Baixar ${artifact.name}`;
      button.dataset.artifactUrl = artifact.url;
      button.dataset.artifactName = artifact.name;
      item.append(button);
      list.append(item);
    }
  }

  private text(id: string, value: string): void {
    this.element<HTMLElement>(id).textContent = value;
  }

  private syncGenerationButton(): void {
    this.element<HTMLButtonElement>("start-generation").disabled =
      this.generationBusy || !this.generationFilesSelected;
  }

  private element<T extends HTMLElement>(id: string): T {
    const element = this.root.querySelector<T>(`#${id}`);
    if (!element) {
      throw new Error(`Elemento obrigatório ausente: ${id}`);
    }
    return element;
  }
}
