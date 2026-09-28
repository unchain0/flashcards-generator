export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface AuthRead {
  authenticated: boolean;
}

export interface NotebookLMStatus {
  authenticated: boolean;
  status: string;
  message: string;
}

export type NotebookConnectionState = "connected" | "disconnected" | "error";

export interface Artifact {
  name: string;
  url: string;
}

export interface GenerationJob {
  id: string;
  status: JobStatus;
  message: string;
  filenames: string[];
  discovered_sources: number;
  completed_sources: number;
  skipped_sources: number;
  failed_sources: number;
  artifacts: Artifact[];
  error: string | null;
}

export interface FlashcardsApi {
  me(): Promise<AuthRead>;
  login(password: string): Promise<AuthRead>;
  logout(): Promise<AuthRead>;
  notebookStatus(): Promise<NotebookLMStatus>;
  startNotebookLogin(): Promise<NotebookLMStatus>;
  createJob(form: FormData): Promise<GenerationJob>;
  getJob(jobId: string): Promise<GenerationJob>;
  download(url: string): Promise<Blob>;
}

export interface DashboardView {
  showAuthentication(visible: boolean): void;
  showDashboard(visible: boolean): void;
  showLogout(visible: boolean): void;
  setAuthenticationMessage(message: string): void;
  setApplicationMessage(message: string): void;
  setNotebookMessage(message: string, state: NotebookConnectionState): void;
  setGenerationEnabled(enabled: boolean): void;
  setGenerationFilesSelected(selected: boolean): void;
  setBusy(action: "login" | "logout" | "connect" | "generate", busy: boolean): void;
  renderJob(job: GenerationJob): void;
  saveArtifact(blob: Blob, name: string): void;
}
