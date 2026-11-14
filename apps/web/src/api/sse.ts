import { getConfig } from "../app/config";
import { ApiError, getToken } from "./client";

export interface SseEvent {
  id: string | null;
  event: string;
  data: unknown;
}

/**
 * Server-sent events over fetch, so the bearer token travels in a header (EventSource cannot send one).
 * Resumes after Last-Event-ID when given. Resolves when the server closes the stream.
 */
export async function streamSse(path: string, onEvent: (e: SseEvent) => void, opts: { lastEventId?: string | null; signal?: AbortSignal } = {}): Promise<void> {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.lastEventId) headers["Last-Event-ID"] = opts.lastEventId;
  const resp = await fetch(`${getConfig().apiBaseUrl}${path}`, { headers, signal: opts.signal });
  if (!resp.ok || !resp.body) throw new Error(`stream failed (${resp.status})`);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const parsed = parseBlock(block);
      if (parsed) onEvent(parsed);
      cut = buffer.indexOf("\n\n");
    }
  }
}

/**
 * A POST whose answer is a stream of server-sent events (the model assistant, the AI pack author): each event is
 * passed on as it arrives. A refused request (4xx/5xx JSON) throws the API's error.
 */
export async function streamPost(path: string, body: unknown, onEvent: (e: SseEvent) => void, opts: { signal?: AbortSignal } = {}): Promise<void> {
  const headers: Record<string, string> = { Accept: "text/event-stream", "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(`${getConfig().apiBaseUrl}${path}`, { method: "POST", headers, body: JSON.stringify(body), signal: opts.signal });
  if (!resp.ok || !resp.body) {
    let err: Record<string, unknown> = {};
    try { err = ((await resp.json()) as { error?: Record<string, unknown> }).error ?? {}; } catch { /* not JSON */ }
    throw new ApiError(resp.status, typeof err.code === "string" ? err.code : "http_error",
      typeof err.message === "string" ? err.message : `The stream failed (${resp.status}).`, null, (err.details as Record<string, unknown>) ?? {});
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut = buffer.indexOf("\n\n");
    while (cut >= 0) {
      const parsed = parseBlock(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
      if (parsed) onEvent(parsed);
      cut = buffer.indexOf("\n\n");
    }
  }
  const rest = parseBlock(buffer.trim());
  if (rest) onEvent(rest);
}

export function parseBlock(block: string): SseEvent | null {
  let id: string | null = null;
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const i = line.indexOf(":");
    const field = i < 0 ? line : line.slice(0, i);
    const value = i < 0 ? "" : line.slice(i + 1).replace(/^ /, "");
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  try {
    return { id, event, data: JSON.parse(data.join("\n")) };
  } catch {
    return { id, event, data: data.join("\n") };
  }
}
