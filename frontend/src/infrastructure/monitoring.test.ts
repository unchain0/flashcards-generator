import { expect, it, vi } from "vitest";
import { init, type EventHint } from "@sentry/browser";
import { initializeMonitoring, sanitizeEvent } from "./monitoring";

vi.mock("@sentry/browser", () => ({
  init: vi.fn(),
  globalHandlersIntegration: () => ({ name: "GlobalHandlers" }),
  browserApiErrorsIntegration: () => ({ name: "BrowserApiErrors" }),
}));

it("sends only fixed error metadata and removes attachments", () => {
  const hint: EventHint = { attachments: [{ filename: "private.pdf", data: "secret" }] };
  const clean = sanitizeEvent(
    {
      type: undefined,
      event_id: "event",
      timestamp: 1,
      level: "error",
      message: "secret password",
      user: { email: "private@example.com" },
      request: { url: "https://private", data: "secret" },
      extra: { cards: "secret" },
      breadcrumbs: [{ message: "secret" }],
      exception: { values: [{ value: "secret document" }] },
    },
    hint,
  );
  expect(clean).toEqual({
    type: undefined,
    event_id: "event",
    timestamp: 1,
    level: "error",
    platform: "javascript",
    release: "flashcards-generator@1.0.0",
    environment: "production",
    message: "Browser application error",
    exception: { values: [{ type: "BrowserError", stacktrace: { frames: [] } }] },
    tags: { service: "flashcards-frontend" },
  });
  expect(hint.attachments).toEqual([]);
});

it("keeps compiled application positions without URLs, symbols, or source context", () => {
  const clean = sanitizeEvent(
    {
      type: undefined,
      exception: {
        values: [
          {
            stacktrace: {
              frames: [
                {
                  filename: "https://private.example/assets/index-abCD_1.js",
                  lineno: 2,
                  colno: 3,
                  function: "secret",
                  context_line: "secret",
                },
                { filename: "/home/private/document.pdf" },
                {},
              ],
            },
          },
        ],
      },
    },
    {},
  );
  expect(clean.exception?.values?.[0]?.stacktrace?.frames).toEqual([
    { filename: "application.js", lineno: 2, colno: 3 },
  ]);
  expect(sanitizeEvent({ type: undefined }, {}).exception?.values).toEqual([]);
  expect(sanitizeEvent({ type: undefined, exception: {} }, {}).exception?.values).toEqual([]);
  expect(
    sanitizeEvent({ type: undefined, exception: { values: [{ stacktrace: {} }] } }, {}).exception
      ?.values?.[0]?.stacktrace?.frames,
  ).toEqual([]);
});

it("uses only error handlers with development transport disabled", () => {
  initializeMonitoring();
  expect(init).toHaveBeenCalledWith(
    expect.objectContaining({
      enabled: false,
      defaultIntegrations: false,
      dataCollection: expect.objectContaining({
        userInfo: false,
        cookies: false,
        httpHeaders: false,
        httpBodies: [],
        urlQueryParams: false,
      }),
      sendClientReports: false,
      maxBreadcrumbs: 0,
      tracesSampleRate: 0,
      beforeSend: sanitizeEvent,
      integrations: [{ name: "GlobalHandlers" }, { name: "BrowserApiErrors" }],
    }),
  );
});
