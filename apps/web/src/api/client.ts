// Same-origin API client. Session = HttpOnly cookie; provider keys never reach the browser.
import i18n from "@/i18n";
import { translateApiError } from "@/i18n/apiErrors";

export interface ErrorBody {
  code: string;
  message: string;
  retryable: boolean;
  correlation_id: string;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: ErrorBody;
  constructor(status: number, body: ErrorBody) {
    super(body.message);
    this.status = status;
    this.body = body;
  }
  get code(): string {
    return this.body.code;
  }
}

/** Host unreachable (offline / host asleep) — distinct from a server error. */
export class OfflineError extends Error {
  constructor() {
    super(i18n.t("errors.hostUnreachable"));
  }
}

export interface WriteOpts {
  /** Idempotency key for this USER INTENT. Reuse the same key when retrying the same action. */
  idempotencyKey: string;
  signal?: AbortSignal;
}

const REQUEST_FAILED = "request failed";

async function parseError(res: Response): Promise<ErrorBody> {
  try {
    return (await res.json()) as ErrorBody;
  } catch {
    return { code: `http_${res.status}`, message: res.statusText || REQUEST_FAILED, retryable: res.status >= 500, correlation_id: "" };
  }
}

async function request<T>(method: string, path: string, body: unknown, opts?: Partial<WriteOpts>): Promise<T> {
  const headers: Record<string, string> = { "x-researchos-client": "web" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method !== "GET") {
    if (!opts?.idempotencyKey) throw new Error(`write ${method} ${path} without an intent idempotency key`);  // i18n-exempt: programming error
    headers["idempotency-key"] = opts.idempotencyKey;
  }
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method, headers, credentials: "same-origin", signal: opts?.signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new OfflineError();
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, { signal }),
  post: <T>(path: string, body: unknown, o: WriteOpts) => request<T>("POST", path, body, o),
  put: <T>(path: string, body: unknown, o: WriteOpts) => request<T>("PUT", path, body, o),
  patch: <T>(path: string, body: unknown, o: WriteOpts) => request<T>("PATCH", path, body, o),
};

/** Plain-language message for any error shown to the user (never a stack trace), in the UI language. Known fixed
 * backend messages are translated by code; any other server message is shown as sent (ADR-221). */
export function describeError(e: unknown): string {
  if (e instanceof OfflineError) return i18n.t("errors.offline");
  if (e instanceof ApiError) {
    const message = e.body.code.startsWith("http_") && e.body.message === REQUEST_FAILED
      ? i18n.t("errors.requestFailed", { status: e.status }) : translateApiError(e.body.code, e.body.message);
    const ref = e.body.correlation_id ? i18n.t("errors.ref", { id: e.body.correlation_id }) : "";
    return `${message}${ref}`;
  }
  return e instanceof Error ? e.message : i18n.t("errors.generic");
}
