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
      file_id?: number;
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
      pending_approvals?: PendingApproval[];
    }
  | { type: 'error'; message: string };

type ApiClientOptions = {
  baseUrl: string;
  getAccessToken: () => string | null;
  onUnauthorized?: (rejectedToken: string | null) => void;
  fetchImpl?: typeof fetch;
  xhrFactory?: () => XMLHttpRequest;
};

type ApiClient = {
  login: (input: LoginInput) => Promise<AuthSession>;
  register: (input: RegisterInput) => Promise<AuthSession>;
  streamMessage: (
    payload: SendMessagePayload,
    onEvent: (event: SSEEvent) => void,
    signal?: AbortSignal,
  ) => Promise<void>;
  getSpreadsheets: () => Promise<SpreadsheetSummary[]>;
  getSpreadsheetInfo: (spreadsheetId?: string) => Promise<SpreadsheetInfo>;
  getSheetData: (
    sheetName: string,
    spreadsheetId?: string,
  ) => Promise<JsonScalar[][]>;
  resolveApproval: (
    approvalId: string,
    decision: ApprovalDecision,
  ) => Promise<ApprovalResolution>;
  health: () => Promise<unknown>;
};

let accessToken: string | null = null;
let unauthorizedHandler: ((rejectedToken: string | null) => void) | undefined;

/** Normalize a host or URL into the versioned Klaudia API base URL. */
export function normalizeApiBaseUrl(configuredUrl: string): string {
  const trimmedUrl = configuredUrl.trim().replace(/\/+$/, '');
  if (!/^https?:\/\//i.test(trimmedUrl)) {
    return `http://${trimmedUrl}:8000/v1`;
  }
  return trimmedUrl.endsWith('/v1') ? trimmedUrl : `${trimmedUrl}/v1`;
}

/** Reject cleartext remote APIs outside local development. */
export function requireSecureApiBaseUrl(
  baseUrl: string,
  isDevelopment: boolean,
): string {
  const hostname = new URL(baseUrl).hostname;
  const isLoopback = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1';
  if (!isDevelopment && !isLoopback && !baseUrl.startsWith('https://')) {
    throw new Error('Klaudia requires an HTTPS API URL outside development.');
  }
  return baseUrl;
}

/** Store the bearer token used by the shared API client. */
export function setApiAccessToken(token: string | null): void {
  accessToken = token;
}

/** Register a callback for an expired or rejected bearer token. */
export function setUnauthorizedHandler(
  handler?: (rejectedToken: string | null) => void,
): void {
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

/** Parse complete SSE frames and return the unfinished trailing frame. */
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

  async function request(
    path: string,
    init?: RequestInit,
    requiresAuth = true,
  ): Promise<unknown> {
    const headers = new Headers(init?.headers);
    headers.set('Content-Type', 'application/json');
    const token = options.getAccessToken();
    if (requiresAuth && token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const response = await fetchImpl(`${baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
      if (response.status === 401 && requiresAuth) {
        options.onUnauthorized?.(token);
      }
      throw new ApiError(response.status, await readErrorMessage(response));
    }
    if (response.status === 204) {
      return null;
    }
    return response.json() as Promise<unknown>;
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
            options.onUnauthorized?.(token);
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
    login: async (input) =>
      requireAuthSessionResponse(
        await request(
          '/auth/login',
          { method: 'POST', body: JSON.stringify(input) },
          false,
        ),
      ),
    register: async (input) =>
      requireAuthSessionResponse(
        await request(
          '/auth/register',
          { method: 'POST', body: JSON.stringify(input) },
          false,
        ),
      ),
    streamMessage,
    getSpreadsheets: async () => parseSpreadsheets(await request('/spreadsheets')),
    getSpreadsheetInfo: async (spreadsheetId) =>
      parseSpreadsheetInfo(
        await request(withQuery('/sheets/info', { spreadsheet_id: spreadsheetId })),
      ),
    getSheetData: async (sheetName, spreadsheetId) => {
      const response = await request(
        withQuery('/sheets/data', {
          sheet: sheetName,
          spreadsheet_id: spreadsheetId,
        }),
      );
      return parseSheetRows(response);
    },
    resolveApproval: async (approvalId, decision) =>
      parseApprovalResolution(
        await request(`/approvals/${encodeURIComponent(approvalId)}`, {
          method: 'POST',
          body: JSON.stringify({ decision }),
        }),
      ),
    health: () => request('/health', undefined, false),
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
  if (dataLines.length === 0) return;

  try {
    const payload: unknown = JSON.parse(dataLines.join('\n'));
    const event = parseSseEvent(eventName, payload);
    if (event !== null) onEvent(event);
  } catch {
    return;
  }
}

function parseSseEvent(eventName: string, payload: unknown): SSEEvent | null {
  if (!isRecord(payload)) return null;

  switch (eventName) {
    case 'session':
      return typeof payload.session_id === 'number'
        ? { type: 'session', session_id: payload.session_id }
        : null;
    case 'guardrail':
      if (typeof payload.stage !== 'string' || typeof payload.status !== 'string') return null;
      if (!isOptionalString(payload.message)) return null;
      return {
        type: 'guardrail',
        stage: payload.stage,
        status: payload.status,
        message: payload.message,
      };
    case 'extraction':
      if (typeof payload.status !== 'string') return null;
      if (
        !isOptionalString(payload.file_name) ||
        !isOptionalNumber(payload.file_id) ||
        !isOptionalNumber(payload.pages) ||
        !isOptionalString(payload.summary) ||
        !isOptionalString(payload.reason)
      ) {
        return null;
      }
      return {
        type: 'extraction',
        status: payload.status,
        file_name: payload.file_name,
        file_id: payload.file_id,
        pages: payload.pages,
        summary: payload.summary,
        reason: payload.reason,
      };
    case 'step':
      return typeof payload.node === 'string' && typeof payload.next === 'string'
        ? { type: 'step', node: payload.node, next: payload.next }
        : null;
    case 'tool':
      return typeof payload.name === 'string' ? { type: 'tool', name: payload.name } : null;
    case 'token':
      return typeof payload.text === 'string' ? { type: 'token', text: payload.text } : null;
    case 'approval_required': {
      const approval = parsePendingApproval(payload);
      return approval === null ? null : { type: 'approval_required', ...approval };
    }
    case 'done': {
      if (
        typeof payload.session_id !== 'number' ||
        typeof payload.processing_time_ms !== 'number' ||
        typeof payload.content !== 'string' ||
        !isStringArray(payload.tools_used)
      ) {
        return null;
      }
      const pendingApprovals = parseOptionalApprovals(payload.pending_approvals);
      if (pendingApprovals === null) return null;
      const doneEvent: SSEEvent = {
        type: 'done',
        session_id: payload.session_id,
        processing_time_ms: payload.processing_time_ms,
        tools_used: payload.tools_used,
        content: payload.content,
      };
      return pendingApprovals === undefined
        ? doneEvent
        : { ...doneEvent, pending_approvals: pendingApprovals };
    }
    case 'error':
      return typeof payload.message === 'string'
        ? { type: 'error', message: payload.message }
        : null;
    default:
      return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Validate the auth record returned by the API or read from storage. */
export function parseAuthSessionValue(value: unknown): AuthSession | null {
  if (
    !isRecord(value) ||
    typeof value.access_token !== 'string' ||
    value.access_token.length === 0 ||
    value.token_type !== 'bearer' ||
    typeof value.user_id !== 'number' ||
    !Number.isInteger(value.user_id) ||
    value.user_id <= 0 ||
    typeof value.username !== 'string' ||
    value.username.length === 0
  ) {
    return null;
  }
  return {
    access_token: value.access_token,
    token_type: value.token_type,
    user_id: value.user_id,
    username: value.username,
  };
}

function requireAuthSessionResponse(value: unknown): AuthSession {
  const session = parseAuthSessionValue(value);
  if (session === null) throw invalidResponseError('auth');
  return session;
}

function parseSpreadsheets(value: unknown): SpreadsheetSummary[] {
  if (!Array.isArray(value)) throw invalidResponseError('spreadsheets');
  const spreadsheets: SpreadsheetSummary[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.spreadsheetId !== 'string' || typeof item.name !== 'string') {
      throw invalidResponseError('spreadsheets');
    }
    spreadsheets.push({ spreadsheetId: item.spreadsheetId, name: item.name });
  }
  return spreadsheets;
}

function parseSpreadsheetInfo(value: unknown): SpreadsheetInfo {
  if (
    !isRecord(value) ||
    typeof value.spreadsheetId !== 'string' ||
    typeof value.title !== 'string' ||
    !Array.isArray(value.sheets)
  ) {
    throw invalidResponseError('spreadsheet info');
  }

  const sheets: SpreadsheetInfo['sheets'] = [];
  for (const sheet of value.sheets) {
    if (
      !isRecord(sheet) ||
      typeof sheet.title !== 'string' ||
      typeof sheet.sheetId !== 'number' ||
      !isRecord(sheet.gridProperties) ||
      typeof sheet.gridProperties.rowCount !== 'number' ||
      typeof sheet.gridProperties.columnCount !== 'number'
    ) {
      throw invalidResponseError('spreadsheet info');
    }
    sheets.push({
      title: sheet.title,
      sheetId: sheet.sheetId,
      gridProperties: {
        rowCount: sheet.gridProperties.rowCount,
        columnCount: sheet.gridProperties.columnCount,
      },
    });
  }
  return { spreadsheetId: value.spreadsheetId, title: value.title, sheets };
}

function parseSheetRows(value: unknown): JsonScalar[][] {
  if (!isRecord(value) || (value.values !== undefined && !Array.isArray(value.values))) {
    throw invalidResponseError('sheet data');
  }
  if (value.values === undefined) return [];

  const rows: JsonScalar[][] = [];
  for (const row of value.values) {
    if (!Array.isArray(row) || !row.every(isJsonScalar)) {
      throw invalidResponseError('sheet data');
    }
    rows.push(row);
  }
  return rows;
}

function parseApprovalResolution(value: unknown): ApprovalResolution {
  if (
    !isRecord(value) ||
    typeof value.approval_id !== 'string' ||
    typeof value.executed !== 'boolean'
  ) {
    throw invalidResponseError('approval');
  }
  return {
    approval_id: value.approval_id,
    executed: value.executed,
    result: value.result,
  };
}

function parsePendingApproval(value: unknown): PendingApproval | null {
  if (
    !isRecord(value) ||
    typeof value.approval_id !== 'string' ||
    typeof value.action !== 'string' ||
    !(value.sheet === null || typeof value.sheet === 'string') ||
    typeof value.summary !== 'string' ||
    typeof value.rows_affected !== 'number' ||
    typeof value.columns_affected !== 'number'
  ) {
    return null;
  }
  return {
    approval_id: value.approval_id,
    action: value.action,
    sheet: value.sheet,
    summary: value.summary,
    rows_affected: value.rows_affected,
    columns_affected: value.columns_affected,
  };
}

function parseOptionalApprovals(value: unknown): PendingApproval[] | undefined | null {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) return null;
  const approvals: PendingApproval[] = [];
  for (const item of value) {
    const approval = parsePendingApproval(item);
    if (approval === null) return null;
    approvals.push(approval);
  }
  return approvals;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isJsonScalar(value: unknown): value is JsonScalar {
  return (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  );
}

function isOptionalString(value: unknown): value is string | undefined {
  return value === undefined || typeof value === 'string';
}

function isOptionalNumber(value: unknown): value is number | undefined {
  return value === undefined || typeof value === 'number';
}

function invalidResponseError(resource: string): ApiError {
  return new ApiError(502, `Invalid ${resource} response from Klaudia.`);
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

const isDevelopment = typeof __DEV__ !== 'undefined' && __DEV__;
export const API_BASE_URL = requireSecureApiBaseUrl(
  normalizeApiBaseUrl(configuredApiUrl),
  isDevelopment,
);

export const api = createApiClient({
  baseUrl: API_BASE_URL,
  getAccessToken: () => accessToken,
  onUnauthorized: (rejectedToken) => unauthorizedHandler?.(rejectedToken),
});

export const {
  login,
  register,
  streamMessage,
  getSpreadsheets,
  getSpreadsheetInfo,
  getSheetData,
  resolveApproval,
  health: healthCheck,
} = api;
