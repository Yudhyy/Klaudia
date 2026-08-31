const configuredApiUrl =
  process.env.EXPO_PUBLIC_API_URL ??
  process.env.EXPO_PUBLIC_BASE_URL ??
  'http://localhost:8000/v1';

export type JsonScalar = string | number | boolean | null;

export type ApiAttachment = {
  filename: string;
  content_type: string;
  data: string;
};

export type ApiMessage = {
  role: 'user' | 'assistant';
  content: string;
  attachments?: ApiAttachment[];
};

export type SendMessagePayload = {
  messages: ApiMessage[];
  session_id?: number;
  user_name?: string;
  spreadsheet_id?: string;
};

export type PendingApproval = {
  approval_id: string;
  action: string;
  sheet: string | null;
  summary: string;
  rows_affected: number;
  columns_affected: number;
};

export type SendMessageResponse = {
  session_id: number;
  message: ApiMessage;
  pending_approvals: PendingApproval[];
};

export type AuthSession = {
  access_token: string;
  token_type: 'bearer';
  user_id: number;
  username: string;
};

export type LoginInput = {
  username: string;
  password: string;
};

export type RegisterInput = LoginInput & {
  email: string;
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

export type SpreadsheetSummary = {
  spreadsheetId: string;
  name: string;
};

export type SpreadsheetInfo = {
  spreadsheetId: string;
  title: string;
  sheets: Array<{
    title: string;
    sheetId: number;
    gridProperties: {
      rowCount: number;
      columnCount: number;
    };
  }>;
};

export type ApprovalDecision = 'approve' | 'reject';

export type ApprovalResolution = {
  approval_id: string;
  executed: boolean;
  result?: unknown;
};

export type SSEEvent =
  | { type: 'session'; session_id: number }
  | { type: 'guardrail'; stage: string; status: string; message?: string }
  | {
      type: 'extraction';
      status: string;
      file_name?: string;
      file_id?: string;
      pages?: number;
      summary?: string;
      reason?: string;
    }
  | { type: 'step'; node: string; next: string }
  | { type: 'tool'; name: string }
  | { type: 'token'; text: string }
  | ({ type: 'approval_required' } & PendingApproval)
  | {
      type: 'done';
      session_id: number;
      processing_time_ms: number;
      tools_used: string[];
      content: string;
      pending_approvals: PendingApproval[];
    }
  | { type: 'error'; message: string };

type ApiClientOptions = {
  baseUrl: string;
  getAccessToken: () => string | null;
  onUnauthorized?: () => void;
  fetchImpl?: typeof fetch;
  xhrFactory?: () => XMLHttpRequest;
};

type ApiClient = {
  login: (input: LoginInput) => Promise<AuthSession>;
  register: (input: RegisterInput) => Promise<AuthSession>;
  sendMessage: (payload: SendMessagePayload) => Promise<SendMessageResponse>;
  streamMessage: (
    payload: SendMessagePayload,
    onEvent: (event: SSEEvent) => void,
    signal?: AbortSignal,
  ) => Promise<void>;
  getSessions: () => Promise<{ sessions: Session[] }>;
  getSession: (sessionId: number) => Promise<SessionDetail>;
  getSpreadsheets: () => Promise<SpreadsheetSummary[]>;
  getSpreadsheetInfo: (spreadsheetId?: string) => Promise<SpreadsheetInfo>;
  getSheetData: (
    sheetName: string,
    spreadsheetId?: string,
  ) => Promise<JsonScalar[][]>;
  getApprovals: () => Promise<PendingApproval[]>;
  resolveApproval: (
    approvalId: string,
    decision: ApprovalDecision,
  ) => Promise<ApprovalResolution>;
  health: () => Promise<unknown>;
};

const SSE_EVENT_NAMES = new Set<SSEEvent['type']>([
  'session',
  'guardrail',
  'extraction',
  'step',
  'tool',
  'token',
  'approval_required',
  'done',
  'error',
]);

let accessToken: string | null = null;
let unauthorizedHandler: (() => void) | undefined;

/** Normalize a host or URL into the versioned Klaudia API base URL. */
export function normalizeApiBaseUrl(configuredUrl: string): string {
  const trimmedUrl = configuredUrl.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(trimmedUrl)) {
    return `http://${trimmedUrl}:8000/v1`;
  }
  return trimmedUrl.endsWith('/v1') ? trimmedUrl : `${trimmedUrl}/v1`;
}

/** Store the bearer token used by the shared API client. */
export function setApiAccessToken(token: string | null): void {
  accessToken = token;
}

/** Register a callback for an expired or rejected bearer token. */
export function setUnauthorizedHandler(handler?: () => void): void {
  unauthorizedHandler = handler;
}

/** HTTP error with the response status retained for auth and UI decisions. */
export class ApiError extends Error {
  public readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Parse complete SSE frames and return any unfinished trailing frame. */
export function parseSseFrames(
  buffer: string,
  onEvent: (event: SSEEvent) => void,
): string {
  const normalizedBuffer = buffer.replace(/\r\n/g, '\n');
  const frames = normalizedBuffer.split('\n\n');
  const remainder = frames.pop() ?? '';

  for (const frame of frames) {
    parseSseFrame(frame, onEvent);
  }
  return remainder;
}

/** Create an API client with injectable transports for deterministic tests. */
export function createApiClient(options: ApiClientOptions): ApiClient {
  const baseUrl = normalizeApiBaseUrl(options.baseUrl);
  const fetchImpl = options.fetchImpl ?? fetch;

  async function request<T>(
    path: string,
    init?: RequestInit,
    requiresAuth = true,
  ): Promise<T> {
    const headers = new Headers(init?.headers);
    headers.set('Content-Type', 'application/json');
    const token = options.getAccessToken();
    if (requiresAuth && token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const response = await fetchImpl(`${baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
      if (response.status === 401 && requiresAuth) {
        options.onUnauthorized?.();
      }
      throw new ApiError(response.status, await readErrorMessage(response));
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  function streamMessage(
    payload: SendMessagePayload,
    onEvent: (event: SSEEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) {
        reject(createAbortError());
        return;
      }

      const xhr = options.xhrFactory?.() ?? new XMLHttpRequest();
      xhr.open('POST', `${baseUrl}/chat/stream`, true);
      xhr.setRequestHeader('Content-Type', 'application/json');
      const token = options.getAccessToken();
      if (token) {
        xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      }

      let processedLength = 0;
      let frameBuffer = '';
      let settled = false;

      const cleanup = (): void => {
        signal?.removeEventListener('abort', abortRequest);
      };

      const finish = (): void => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve();
      };

      const fail = (error: Error): void => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(error);
      };

      function abortRequest(): void {
        xhr.abort();
      }

      const readProgress = (): void => {
        const nextChunk = xhr.responseText.slice(processedLength);
        processedLength = xhr.responseText.length;
        frameBuffer = parseSseFrames(frameBuffer + nextChunk, onEvent);
      };

      signal?.addEventListener('abort', abortRequest);
      xhr.onprogress = readProgress;
      xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) {
          if (xhr.status === 401) {
            options.onUnauthorized?.();
          }
          fail(new ApiError(xhr.status, `Request failed with status ${xhr.status}`));
          return;
        }
        readProgress();
        if (frameBuffer.trim()) {
          parseSseFrames(`${frameBuffer}\n\n`, onEvent);
        }
        finish();
      };
      xhr.onerror = () => fail(new Error('Tidak dapat terhubung ke server Klaudia.'));
      xhr.onabort = () => fail(createAbortError());
      xhr.send(JSON.stringify(payload));
    });
  }

  return {
    login: (input) =>
      request<AuthSession>(
        '/auth/login',
        { method: 'POST', body: JSON.stringify(input) },
        false,
      ),
    register: (input) =>
      request<AuthSession>(
        '/auth/register',
        { method: 'POST', body: JSON.stringify(input) },
        false,
      ),
    sendMessage: (payload) =>
      request<SendMessageResponse>('/chat', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    streamMessage,
    getSessions: () => request<{ sessions: Session[] }>('/sessions'),
    getSession: (sessionId) => request<SessionDetail>(`/sessions/${sessionId}`),
    getSpreadsheets: () => request<SpreadsheetSummary[]>('/spreadsheets'),
    getSpreadsheetInfo: (spreadsheetId) =>
      request<SpreadsheetInfo>(withQuery('/sheets/info', { spreadsheet_id: spreadsheetId })),
    getSheetData: async (sheetName, spreadsheetId) => {
      const response = await request<{ values?: JsonScalar[][] }>(
        withQuery('/sheets/data', {
          sheet: sheetName,
          spreadsheet_id: spreadsheetId,
        }),
      );
      return response.values ?? [];
    },
    getApprovals: () => request<PendingApproval[]>('/approvals'),
    resolveApproval: (approvalId, decision) =>
      request<ApprovalResolution>(`/approvals/${encodeURIComponent(approvalId)}`, {
        method: 'POST',
        body: JSON.stringify({ decision }),
      }),
    health: () => request<unknown>('/health', undefined, false),
  };
}

function parseSseFrame(
  frame: string,
  onEvent: (event: SSEEvent) => void,
): void {
  if (!frame.trim()) return;

  let eventName = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!isSseEventName(eventName) || dataLines.length === 0) return;

  try {
    const payload: unknown = JSON.parse(dataLines.join('\n'));
    if (isRecord(payload)) {
      onEvent({ ...payload, type: eventName } as SSEEvent);
    }
  } catch {
    return;
  }
}

function isSseEventName(value: string): value is SSEEvent['type'] {
  return SSE_EVENT_NAMES.has(value as SSEEvent['type']);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function withQuery(
  path: string,
  parameters: Record<string, string | undefined>,
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(parameters)) {
    if (value !== undefined) query.set(key, value);
  }
  const encodedQuery = query.toString();
  return encodedQuery ? `${path}?${encodedQuery}` : path;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isRecord(payload) && typeof payload.detail === 'string') {
      return payload.detail;
    }
  } catch {
    return `Request failed with status ${response.status}`;
  }
  return `Request failed with status ${response.status}`;
}

function createAbortError(): Error {
  return Object.assign(new Error('Aborted'), { name: 'AbortError' });
}

export const API_BASE_URL = normalizeApiBaseUrl(configuredApiUrl);

export const api = createApiClient({
  baseUrl: API_BASE_URL,
  getAccessToken: () => accessToken,
  onUnauthorized: () => unauthorizedHandler?.(),
});

export const {
  login,
  register,
  sendMessage,
  streamMessage,
  getSessions,
  getSession,
  getSpreadsheets,
  getSpreadsheetInfo,
  getSheetData,
  getApprovals,
  resolveApproval,
  health: healthCheck,
} = api;
