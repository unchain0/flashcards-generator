import {
  HttpApiError,
  type Artifact,
  type AuthRead,
  type FlashcardsApi,
  type GenerationJob,
  type JobStatus,
  type NotebookLMStatus,
} from "../domain/contracts";

type Fetcher = typeof fetch;
type JsonValidator = (value: unknown) => boolean;
type Validators<T> = { [Key in keyof T]-?: JsonValidator };

const COMPANION_ORIGIN = "http://127.0.0.1:8765";
const JOB_STATUSES: ReadonlySet<string> = new Set<JobStatus>([
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
]);

interface CompanionTokenRead {
  access_token: string;
  expires_in: number;
}

const isBoolean: JsonValidator = (value) => typeof value === "boolean";
const isString: JsonValidator = (value) => typeof value === "string";
const isNullableString: JsonValidator = (value) => value === null || isString(value);
const isNonNegativeInteger: JsonValidator = (value) =>
  Number.isInteger(value) && typeof value === "number" && value >= 0;
const isPositiveInteger: JsonValidator = (value) => isNonNegativeInteger(value) && value !== 0;
const isJobStatus: JsonValidator = (value) => typeof value === "string" && JOB_STATUSES.has(value);
const isStringArray: JsonValidator = (value) => Array.isArray(value) && value.every(isString);
const isArtifact = (value: unknown): value is Artifact =>
  isRecord(value) && isString(value["name"]) && isString(value["url"]);
const isArtifactArray: JsonValidator = (value) => Array.isArray(value) && value.every(isArtifact);

const AUTH_READ_VALIDATORS: Validators<AuthRead> = {
  authenticated: isBoolean,
};
const NOTEBOOK_STATUS_VALIDATORS: Validators<NotebookLMStatus> = {
  authenticated: isBoolean,
  status: isString,
  message: isString,
};
const COMPANION_TOKEN_VALIDATORS: Validators<CompanionTokenRead> = {
  access_token: isString,
  expires_in: isPositiveInteger,
};
const GENERATION_JOB_VALIDATORS: Validators<GenerationJob> = {
  id: isString,
  status: isJobStatus,
  message: isString,
  filenames: isStringArray,
  discovered_sources: isNonNegativeInteger,
  completed_sources: isNonNegativeInteger,
  skipped_sources: isNonNegativeInteger,
  failed_sources: isNonNegativeInteger,
  artifacts: isArtifactArray,
  error: isNullableString,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseObject<T>(value: unknown, validators: Validators<T>, source: string): T {
  if (!isRecord(value)) {
    throw invalidResponse(source);
  }
  const entries = Object.entries(validators) as Array<[keyof T, JsonValidator]>;
  if (!entries.every(([key, validate]) => validate(value[String(key)]))) {
    throw invalidResponse(source);
  }
  return value as T;
}

function invalidResponse(source: string): HttpApiError {
  return new HttpApiError(`Resposta inválida de ${source}.`, 502);
}

function parseAuthRead(value: unknown): AuthRead {
  return parseObject(value, AUTH_READ_VALIDATORS, "autenticação");
}

function parseNotebookStatus(value: unknown): NotebookLMStatus {
  return parseObject(value, NOTEBOOK_STATUS_VALIDATORS, "NotebookLM");
}

function parseCompanionToken(value: unknown): CompanionTokenRead {
  return parseObject(value, COMPANION_TOKEN_VALIDATORS, "autorização local");
}

function parseGenerationJob(value: unknown): GenerationJob {
  return parseObject(value, GENERATION_JOB_VALIDATORS, "geração");
}

export class HttpFlashcardsApi implements FlashcardsApi {
  constructor(private readonly fetcher: Fetcher = globalThis.fetch.bind(globalThis)) {}

  me(): Promise<AuthRead> {
    return this.request("/api/v1/auth/me", parseAuthRead);
  }

  login(password: string): Promise<AuthRead> {
    return this.request("/api/v1/auth/login", parseAuthRead, {
      method: "POST",
      body: JSON.stringify({ password }),
    });
  }

  logout(): Promise<AuthRead> {
    return this.request("/api/v1/auth/logout", parseAuthRead, { method: "POST" });
  }

  notebookStatus(): Promise<NotebookLMStatus> {
    return this.requestCompanion("/v1/notebooklm/status", parseNotebookStatus);
  }

  startNotebookLogin(): Promise<NotebookLMStatus> {
    return this.requestCompanion("/v1/notebooklm/login", parseNotebookStatus, {
      method: "POST",
    });
  }

  createJob(form: FormData): Promise<GenerationJob> {
    return this.requestCompanion("/v1/jobs", parseGenerationJob, {
      method: "POST",
      body: form,
    });
  }

  getJob(jobId: string): Promise<GenerationJob> {
    return this.requestCompanion(`/v1/jobs/${encodeURIComponent(jobId)}`, parseGenerationJob);
  }

  async download(url: string): Promise<Blob> {
    return (await this.companionResponse(url)).blob();
  }

  private async request<T>(
    path: string,
    parser: (value: unknown) => T,
    options: RequestInit = {},
  ): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.body && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    const response = await this.fetcher(path, {
      ...options,
      headers,
      credentials: "same-origin",
    });
    if (!response.ok) {
      throw await this.errorFrom(response);
    }
    return parser(await response.json());
  }

  private async requestCompanion<T>(
    path: string,
    parser: (value: unknown) => T,
    options: RequestInit = {},
  ): Promise<T> {
    return parser(await (await this.companionResponse(path, options)).json());
  }

  private async companionResponse(path: string, options: RequestInit = {}): Promise<Response> {
    const { access_token: accessToken } = await this.request(
      "/api/v1/auth/companion/token",
      parseCompanionToken,
      { method: "POST" },
    );
    const headers = new Headers(options.headers);
    headers.set("Authorization", `Bearer ${accessToken}`);
    let response: Response;
    try {
      response = await this.fetcher(`${COMPANION_ORIGIN}${path}`, {
        ...options,
        headers,
        credentials: "omit",
      });
    } catch (error) {
      if (error instanceof TypeError) {
        throw new HttpApiError(
          "O Flashcards Companion não respondeu. Inicie o auxiliar e autorize o acesso local no navegador.",
          0,
        );
      }
      throw error;
    }
    if (!response.ok) {
      throw await this.errorFrom(response);
    }
    return response;
  }

  private async errorFrom(response: Response): Promise<HttpApiError> {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch (error) {
      if (!(error instanceof SyntaxError)) {
        throw error;
      }
    }
    const detail =
      typeof body === "object" && body !== null && "detail" in body ? body.detail : undefined;
    const message = typeof detail === "string" ? detail : `Falha HTTP ${response.status}`;
    return new HttpApiError(message, response.status);
  }
}
