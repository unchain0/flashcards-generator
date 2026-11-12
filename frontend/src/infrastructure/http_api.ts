import {
  HttpApiError,
  type AuthRead,
  type FlashcardsApi,
  type NotebookLMStatus,
  type ServerJobCreated,
  type ServerJobStatus,
} from "../domain/contracts";

type Fetcher = typeof fetch;
type JsonValidator = (value: unknown) => boolean;
type Validators<T> = { [Key in keyof T]-?: JsonValidator };

const COMPANION_ORIGIN = "http://127.0.0.1:8766";
const SERVER_JOB_STATUSES: ReadonlySet<string> = new Set([
  "queued",
  "running",
  "done",
  "completed",
  "failed",
]);

interface CompanionTokenRead {
  access_token: string;
  expires_in: number;
}

const isBoolean: JsonValidator = (value) => typeof value === "boolean";
const isString: JsonValidator = (value) => typeof value === "string";
const isNonNegativeInteger: JsonValidator = (value) =>
  Number.isInteger(value) && typeof value === "number" && value >= 0;
const isPositiveInteger: JsonValidator = (value) => isNonNegativeInteger(value) && value !== 0;
const isServerJobStatus: JsonValidator = (value) =>
  typeof value === "string" && SERVER_JOB_STATUSES.has(value);

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
const SERVER_JOB_CREATED_VALIDATORS: Validators<ServerJobCreated> = {
  job_id: isString,
};
const SERVER_JOB_STATUS_VALIDATORS: Validators<ServerJobStatus> = {
  status: isServerJobStatus,
  cards: isNonNegativeInteger,
  download_url: isString,
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

function parseServerJobCreated(value: unknown): ServerJobCreated {
  return parseObject(value, SERVER_JOB_CREATED_VALIDATORS, "geração");
}

function parseServerJobStatus(value: unknown): ServerJobStatus {
  return parseObject(value, SERVER_JOB_STATUS_VALIDATORS, "geração");
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

  createServerJob(form: FormData): Promise<ServerJobCreated> {
    return this.request("/api/v1/generate", parseServerJobCreated, {
      method: "POST",
      body: form,
    });
  }

  getServerJob(jobId: string): Promise<ServerJobStatus> {
    return this.request(`/api/v1/jobs/${encodeURIComponent(jobId)}`, parseServerJobStatus);
  }

  async downloadServer(url: string): Promise<Blob> {
    const response = await this.fetcher(url, { credentials: "same-origin" });
    if (!response.ok) {
      throw await this.errorFrom(response);
    }
    return response.blob();
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
