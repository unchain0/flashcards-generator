import {
  browserApiErrorsIntegration,
  globalHandlersIntegration,
  init,
  type ErrorEvent,
  type EventHint,
} from "@sentry/browser";

export function sanitizeEvent(event: ErrorEvent, hint: EventHint): ErrorEvent {
  hint.attachments = [];
  return {
    type: undefined,
    event_id: event.event_id,
    timestamp: event.timestamp,
    level: event.level,
    platform: "javascript",
    release: "flashcards-generator@1.0.0",
    environment: "production",
    message: "Browser application error",
    exception: {
      values: (event.exception?.values ?? []).map((exception) => ({
        type: "BrowserError",
        stacktrace: {
          frames: (exception.stacktrace?.frames ?? [])
            .filter((frame) => /\/assets\/index-[\w-]+\.js$/.test(frame.filename ?? ""))
            .map((frame) => ({
              filename: "application.js",
              lineno: frame.lineno,
              colno: frame.colno,
            })),
        },
      })),
    },
    tags: { service: "flashcards-frontend" },
  };
}

export function initializeMonitoring(): void {
  init({
    dsn: "https://ec01c3653823db63fefa19aaa1fff01d@o4505598204248064.ingest.us.sentry.io/4512186411581440",
    enabled: import.meta.env.PROD,
    environment: "production",
    release: "flashcards-generator@1.0.0",
    defaultIntegrations: false,
    integrations: [globalHandlersIntegration(), browserApiErrorsIntegration()],
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
      graphQL: { document: false, variables: false },
      genAI: { inputs: false, outputs: false },
    },
    sendClientReports: false,
    maxBreadcrumbs: 0,
    tracesSampleRate: 0,
    beforeSend: sanitizeEvent,
  });
}
