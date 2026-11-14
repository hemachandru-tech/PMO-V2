import { getConfig } from "../app/config";

/** Error envelope returned by every Tela API: {"error": {code, message, request_id, details}}. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string | null,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type TokenSource = () => string | null;
let tokenSource: TokenSource = () => null;

/** The access token lives in memory only (never localStorage); the auth layer registers a getter. */
export function setTokenSource(source: TokenSource): void {
  tokenSource = source;
}

export function getToken(): string | null {
  return tokenSource();
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON, or FormData for a file upload (the browser sets the multipart boundary). */
  body?: unknown;
  ifMatch?: string;
  /** Makes a retried POST safe: the API replays the first answer for the same key (decisions, requests). */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface ApiResponse<T> {
  data: T;
  etag: string | null;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<ApiResponse<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenSource();
  if (token) headers.Authorization = `Bearer ${token}`;
  const form = opts.body instanceof FormData;
  if (opts.body !== undefined && !form) headers["Content-Type"] = "application/json";
  if (opts.ifMatch) headers["If-Match"] = opts.ifMatch;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  const resp = await fetch(`${getConfig().apiBaseUrl}${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body === undefined ? undefined : form ? (opts.body as FormData) : JSON.stringify(opts.body),
    signal: opts.signal,
  });

  if (resp.status === 204) return { data: undefined as T, etag: resp.headers.get("ETag") };

  let payload: unknown = null;
  try {
    payload = await resp.json();
  } catch {
    payload = null;
  }
  if (!resp.ok) {
    const err = (payload as { error?: Record<string, unknown> } | null)?.error ?? {};
    throw new ApiError(
      resp.status,
      typeof err.code === "string" ? err.code : "http_error",
      typeof err.message === "string" ? err.message : "Something went wrong.",
      typeof err.request_id === "string" ? err.request_id : resp.headers.get("x-request-id"),
      (err.details as Record<string, unknown>) ?? {},
    );
  }
  return { data: payload as T, etag: resp.headers.get("ETag") };
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return (await apiRequest<T>(path, opts)).data;
}
