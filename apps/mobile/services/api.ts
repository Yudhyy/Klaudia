// services/api.ts
const IP = process.env.EXPO_PUBLIC_BASE_URL ?? '192.168.1.4';
const BASE = `http://${IP}:8000/v1`;

// ─── Types ────────────────────────────────────────────────────────────────────

export type ApiAttachment = {
  filename: string;
  content_type: string;
  data: string; // base64
};

export type ApiMessage = {
  role: 'user' | 'assistant';
  content: string;
  attachments?: ApiAttachment[];
};

export type SendMessagePayload = {
  messages: ApiMessage[];
  session_id?: number;
  user_id?: number;
  user_name?: string;
};

export type SendMessageResponse = {
  session_id: number;
  message: { role: string; content: string };
};

export type Session = {
  session_id: number;
  session_name: string | null;
  created_at: string;
  updated_at: string;
};

export type SessionDetail = {
  session_id: number;
  messages: Array<{
    sender: 'user' | 'assistant';
    message_text: string;
    timestamp: string;
  }>;
};

export type SpreadsheetInfo = {
  spreadsheetId: string;
  title: string;
  sheets: Array<{ title: string; sheetId: number; index: number }>;
};

// SSE event types — merged form after parsing: { type, ...data }
//
// Wire format from backend (_format_sse in chat.py):
//   event: token
//   data: {"text": " hello"}
//
// We merge into: { type: "token", text: " hello" }
export type SSEEvent =
  | { type: 'session';    session_id: number }
  | { type: 'guardrail'; stage: string; status: string; message?: string }
  | { type: 'extraction'; status: string; file_name?: string; file_id?: string; pages?: number; summary?: string; reason?: string }
  | { type: 'step';       node: string; next: string }
  | { type: 'tool';       name: string }
  | { type: 'token';      text: string }
  | { type: 'done';       session_id: number; processing_time_ms: number; tools_used: string[]; content: string }
  | { type: 'error';      message: string };

// ─── SSE frame parser (shared between XHR onprogress chunks) ─────────────────

function parseFrames(
  buffer: string,
  onEvent: (e: SSEEvent) => void,
): string {
  // SSE frames are separated by a blank line (\n\n)
  const frames = buffer.split('\n\n');
  const remaining = frames.pop() ?? ''; // incomplete trailing frame

  for (const frame of frames) {
    if (!frame.trim()) continue;

    let eventType = 'message';
    let dataStr = '';

    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) eventType = line.slice(6).trim();
      else if (line.startsWith('data:')) dataStr = line.slice(5).trim();
    }

    if (!dataStr) continue;

    try {
      const data = JSON.parse(dataStr);
      onEvent({ type: eventType, ...data } as SSEEvent);
    } catch {
      // malformed JSON in frame — skip
    }
  }

  return remaining;
}

// ─── Core fetch helper (non-streaming endpoints) ──────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${body || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

const DEFAULT_USER = { user_id: 1, user_name: 'Ryuuky' };

/** Non-streaming — POST /v1/chat */
export async function sendMessage(payload: SendMessagePayload): Promise<SendMessageResponse> {
  return apiFetch<SendMessageResponse>('/chat', {
    method: 'POST',
    body: JSON.stringify({ ...DEFAULT_USER, ...payload }),
  });
}

/**
 * Streaming chat — POST /v1/chat/stream
 *
 * Uses XMLHttpRequest instead of fetch because React Native / Expo Go does
 * NOT support res.body (ReadableStream) — res.body is always null in RN's
 * fetch implementation. XHR's onprogress fires as chunks arrive with the
 * full cumulative responseText, which we slice to get only new bytes.
 *
 * Wire format from backend (_format_sse in chat.py):
 *   event: token
 *   data: {"text": " hello"}
 *
 * Each frame is merged into: { type: "token", text: " hello" }
 */
export function streamMessage(
  payload: SendMessagePayload,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    // Bail immediately if already aborted before we even open the request
    if (signal?.aborted) {
      reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
      return;
    }

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BASE}/chat/stream`, /* async */ true);
    xhr.setRequestHeader('Content-Type', 'application/json');

    // Wire AbortSignal → xhr.abort()
    const onAbort = () => xhr.abort();
    signal?.addEventListener('abort', onAbort);

    const cleanup = () => signal?.removeEventListener('abort', onAbort);

    // responseText is cumulative — track how many bytes we've already parsed
    let processed = 0;
    let buffer = '';

    xhr.onprogress = () => {
      const newChunk = xhr.responseText.slice(processed);
      processed = xhr.responseText.length;
      buffer += newChunk;
      buffer = parseFrames(buffer, onEvent);
    };

    xhr.onload = () => {
      // Flush any bytes that arrived after the last onprogress
      const tail = xhr.responseText.slice(processed);
      if (tail) parseFrames(tail, onEvent);
      cleanup();
      resolve();
    };

    xhr.onerror = () => {
      cleanup();
      reject(new Error('Network error — check BASE_URL and that the backend is reachable'));
    };

    xhr.onabort = () => {
      cleanup();
      reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
    };

    xhr.send(JSON.stringify({ ...DEFAULT_USER, ...payload }));
  });
}

// ─── Sessions ─────────────────────────────────────────────────────────────────

export async function getSessions(): Promise<{ sessions: Session[] }> {
  return apiFetch<{ sessions: Session[] }>('/sessions?user_id=1');
}

export async function getSession(sessionId: number): Promise<SessionDetail> {
  return apiFetch<SessionDetail>(`/sessions/${sessionId}`);
}

// ─── Sheets ───────────────────────────────────────────────────────────────────

export async function getSpreadsheetInfo(): Promise<SpreadsheetInfo> {
  return apiFetch<SpreadsheetInfo>('/sheets/info');
}

export async function getSheetData(sheetName: string): Promise<string[][]> {
  const json = await apiFetch<{ values?: string[][] }>(
    `/sheets/data?sheet=${encodeURIComponent(sheetName)}`
  );
  return json.values ?? [];
}

// ─── Health ───────────────────────────────────────────────────────────────────

export async function healthCheck(): Promise<unknown> {
  return apiFetch('/health');
}

// ─── Barrel export (backward compat) ─────────────────────────────────────────

export const api = {
  sendMessage,
  streamMessage,
  getSessions,
  getSession,
  getSpreadsheetInfo,
  getSheetData,
  health: healthCheck,
};