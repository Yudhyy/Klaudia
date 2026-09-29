import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiError,
  createApiClient,
  normalizeApiBaseUrl,
  parseSseFrames,
  requireSecureApiBaseUrl,
  type SSEEvent,
} from './api.ts';

test('normalizes a host and full API URLs', () => {
  assert.equal(normalizeApiBaseUrl('192.168.1.20'), 'http://192.168.1.20:8000/v1');
  assert.equal(normalizeApiBaseUrl('https://api.example.com'), 'https://api.example.com/v1');
  assert.equal(normalizeApiBaseUrl('https://api.example.com/v1/'), 'https://api.example.com/v1');
});

test('rejects a cleartext remote API outside development', () => {
  assert.equal(
    requireSecureApiBaseUrl('http://192.168.1.20:8000/v1', true),
    'http://192.168.1.20:8000/v1',
  );
  assert.equal(
    requireSecureApiBaseUrl('http://localhost:8000/v1', false),
    'http://localhost:8000/v1',
  );
  assert.throws(
    () => requireSecureApiBaseUrl('http://api.example.com/v1', false),
    /HTTPS API URL/,
  );
});

test('adds the bearer token and spreadsheet scope without a user id', async () => {
  let requestUrl = '';
  let requestInit: RequestInit | undefined;
  const fetchImpl: typeof fetch = async (input, init) => {
    requestUrl = String(input);
    requestInit = init;
    return Response.json({ values: [['Total'], [12500]] });
  };
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'secret-token',
    fetchImpl,
  });

  const values = await client.getSheetData('Rangkuman Total', 'ledger-123');

  assert.deepEqual(values, [['Total'], [12500]]);
  assert.equal(
    requestUrl,
    'http://localhost:8000/v1/sheets/data?sheet=Rangkuman+Total&spreadsheet_id=ledger-123',
  );
  const headers = new Headers(requestInit?.headers);
  assert.equal(headers.get('Authorization'), 'Bearer secret-token');
  assert.equal(requestUrl.includes('user_id'), false);
});

test('validates auth, ledger, sheet info, and approval responses', async () => {
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'token',
    fetchImpl: async (input) => {
      const url = String(input);
      if (url.endsWith('/auth/login')) {
        return Response.json({
          access_token: 'jwt-token',
          token_type: 'bearer',
          user_id: 8,
          username: 'klaudia-user',
        });
      }
      if (url.endsWith('/spreadsheets')) {
        return Response.json([{ spreadsheetId: 'ledger-123', name: 'Utama' }]);
      }
      if (url.includes('/sheets/info')) {
        return Response.json({
          spreadsheetId: 'ledger-123',
          title: 'Klaudia Ledger',
          sheets: [
            {
              title: 'Jan',
              sheetId: 1,
              gridProperties: { rowCount: 3, columnCount: 2 },
            },
          ],
        });
      }
      if (url.includes('/approvals/')) {
        return Response.json({ approval_id: 'approval-1', executed: true });
      }
      return Response.json({ detail: 'Not found' }, { status: 404 });
    },
  });

  assert.equal((await client.login({ username: 'klaudia-user', password: 'password' })).user_id, 8);
  assert.deepEqual(await client.getSpreadsheets(), [
    { spreadsheetId: 'ledger-123', name: 'Utama' },
  ]);
  assert.equal((await client.getSpreadsheetInfo('ledger-123')).sheets[0]?.title, 'Jan');
  assert.equal((await client.resolveApproval('approval-1', 'approve')).executed, true);
});

test('calls the unauthorized handler and rejects a 401 response', async () => {
  let rejectedToken: string | null | undefined;
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'expired-token',
    onUnauthorized: (token) => {
      rejectedToken = token;
    },
    fetchImpl: async () => Response.json({ detail: 'Invalid token' }, { status: 401 }),
  });

  await assert.rejects(client.getSpreadsheets(), (error: unknown) => {
    assert.equal(error instanceof ApiError, true);
    assert.equal((error as ApiError).status, 401);
    return true;
  });
  assert.equal(rejectedToken, 'expired-token');
});

test('rejects malformed sheet data returned by the server', async () => {
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'token',
    fetchImpl: async () => Response.json({ values: [[{ unsafe: true }]] }),
  });

  await assert.rejects(client.getSheetData('Jun', 'ledger-123'), (error: unknown) => {
    assert.equal(error instanceof ApiError, true);
    assert.equal((error as ApiError).status, 502);
    return true;
  });
});

test('rejects sheet responses without the required values grid', async () => {
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'token',
    fetchImpl: async () => Response.json({ spreadsheetId: 'ledger-123' }),
  });

  await assert.rejects(client.getSheetData('Expenses', 'ledger-123'), ApiError);
});

test('keeps an incomplete SSE frame for the next chunk', () => {
  const events: SSEEvent[] = [];
  const firstChunk = [
    'event: session',
    'data: {"session_id":14}',
    '',
    'event: approval_required',
    'data: {"approval_id":"approve-1",',
  ].join('\n');

  const remainder = parseSseFrames(firstChunk, (event) => events.push(event));

  assert.deepEqual(events, [{ type: 'session', session_id: 14 }]);
  assert.match(remainder, /approval_required/);

  const finalRemainder = parseSseFrames(
    `${remainder}"action":"checked_table_append","operation_ref":"prepared:1","proposal":{"table_id":"expenses","records":[{"Amount":12500}]},"summary":"Append 1 record","rows_affected":1,"columns_affected":1,"expires_at":"2026-09-29T12:00:00+00:00"}\n\n`,
    (event) => events.push(event),
  );

  assert.equal(finalRemainder, '');
  assert.deepEqual(events[1], {
    type: 'approval_required',
    approval_id: 'approve-1',
    action: 'checked_table_append',
    operation_ref: 'prepared:1',
    proposal: { table_id: 'expenses', records: [{ Amount: 12500 }] },
    summary: 'Append 1 record',
    rows_affected: 1,
    columns_affected: 1,
    expires_at: '2026-09-29T12:00:00+00:00',
  });
});

test('rejects an approval event missing its exact proposal', () => {
  const events: SSEEvent[] = [];
  parseSseFrames(
    'event: approval_required\ndata: {"approval_id":"approve-1","action":"checked_table_append","operation_ref":"prepared:1","summary":"Append","rows_affected":1,"columns_affected":1,"expires_at":"2026-09-29T12:00:00+00:00"}\n\n',
    (event) => events.push(event),
  );
  assert.deepEqual(events, []);
});

test('keeps durable task identity and status from the final stream event', () => {
  const events: SSEEvent[] = [];
  parseSseFrames(
    'event: done\ndata: {"session_id":9,"processing_time_ms":12,"tools_used":[],"content":"Approval needed","pending_approvals":[],"task_id":"task:123","run_status":"awaiting_approval"}\n\n',
    (event) => events.push(event),
  );

  assert.deepEqual(events, [{
    type: 'done',
    session_id: 9,
    processing_time_ms: 12,
    tools_used: [],
    content: 'Approval needed',
    pending_approvals: [],
    task_id: 'task:123',
    run_status: 'awaiting_approval',
  }]);
});

test('resumes a durable task with the backend response contract', async () => {
  let requestUrl = '';
  let requestInit: RequestInit | undefined;
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'token',
    fetchImpl: async (input, init) => {
      requestUrl = String(input);
      requestInit = init;
      return Response.json({
        message: { role: 'assistant', content: 'The row was added.' },
        session_id: 9,
        processing_time_ms: 12,
        tools_used: [],
        pending_approvals: [],
        task_id: 'task:123',
        run_status: 'answered',
      });
    },
  });

  const response = await client.resumeTask('task:123');

  assert.equal(requestUrl, 'http://localhost:8000/v1/tasks/task%3A123/resume');
  assert.equal(requestInit?.method, 'POST');
  assert.equal(response.message.content, 'The row was added.');
  assert.equal(response.run_status, 'answered');
});

test('ignores malformed SSE payloads instead of trusting their event names', () => {
  const events: SSEEvent[] = [];

  parseSseFrames(
    [
      'event: token',
      'data: {"text":42}',
      '',
      'event: done',
      'data: {"session_id":"wrong","content":null}',
      '',
      '',
    ].join('\n'),
    (event) => events.push(event),
  );

  assert.deepEqual(events, []);
});

test('accepts extraction progress with the numeric backend file id', () => {
  const events: SSEEvent[] = [];

  parseSseFrames(
    'event: extraction\ndata: {"status":"queued","file_id":42,"pages":3}\n\n',
    (event) => events.push(event),
  );

  assert.deepEqual(events, [
    {
      type: 'extraction',
      status: 'queued',
      file_name: undefined,
      file_id: 42,
      pages: 3,
      summary: undefined,
      reason: undefined,
    },
  ]);
});

test('streams chunked events with bearer auth and ledger scope', async () => {
  const transport = new MockXmlHttpRequest();
  const events: SSEEvent[] = [];
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'stream-token',
    xhrFactory: () => transport as unknown as XMLHttpRequest,
  });

  const stream = client.streamMessage(
    {
      messages: [{ role: 'user', content: 'Ringkas ledger' }],
      spreadsheet_id: 'ledger-123',
    },
    (event) => events.push(event),
  );
  transport.responseText = 'event: token\ndata: {"text":"Halo"}\n';
  transport.onprogress?.();
  transport.responseText += '\nevent: done\ndata: {"session_id":9,"processing_time_ms":12,"tools_used":[],"content":"Halo"}\n\n';
  transport.status = 200;
  transport.onload?.();
  await stream;

  assert.equal(transport.method, 'POST');
  assert.equal(transport.url, 'http://localhost:8000/v1/chat/stream');
  assert.equal(transport.headers.get('Authorization'), 'Bearer stream-token');
  assert.match(transport.body, /"spreadsheet_id":"ledger-123"/);
  assert.deepEqual(events, [
    { type: 'token', text: 'Halo' },
    {
      type: 'done',
      session_id: 9,
      processing_time_ms: 12,
      tools_used: [],
      content: 'Halo',
    },
  ]);
});

test('logs out after an unauthorized stream response', async () => {
  const transport = new MockXmlHttpRequest();
  let rejectedToken: string | null | undefined;
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'expired-token',
    onUnauthorized: (token) => {
      rejectedToken = token;
    },
    xhrFactory: () => transport as unknown as XMLHttpRequest,
  });

  const stream = client.streamMessage(
    { messages: [{ role: 'user', content: 'Hello' }] },
    () => undefined,
  );
  transport.status = 401;
  transport.onload?.();

  await assert.rejects(stream, (error: unknown) => {
    assert.equal(error instanceof ApiError, true);
    assert.equal((error as ApiError).status, 401);
    return true;
  });
  assert.equal(rejectedToken, 'expired-token');
});

test('rejects a stream that ends without a final event', async () => {
  const transport = new MockXmlHttpRequest();
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'token',
    xhrFactory: () => transport as unknown as XMLHttpRequest,
  });

  const stream = client.streamMessage({ messages: [{ role: 'user', content: 'Hello' }] }, () => undefined);
  transport.responseText = 'event: token\ndata: {"text":"Partial"}\n\n';
  transport.status = 200;
  transport.onload?.();

  await assert.rejects(stream, /before sending a final response/);
});

test('rejects a server error event even when HTTP status is successful', async () => {
  const transport = new MockXmlHttpRequest();
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'token',
    xhrFactory: () => transport as unknown as XMLHttpRequest,
  });

  const stream = client.streamMessage({ messages: [{ role: 'user', content: 'Hello' }] }, () => undefined);
  transport.responseText = 'event: error\ndata: {"message":"Task stopped"}\n\n';
  transport.status = 200;
  transport.onload?.();

  await assert.rejects(stream, /Task stopped/);
});

test('aborts an active stream when its signal is cancelled', async () => {
  const transport = new MockXmlHttpRequest();
  const controller = new AbortController();
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'token',
    xhrFactory: () => transport as unknown as XMLHttpRequest,
  });

  const stream = client.streamMessage(
    { messages: [{ role: 'user', content: 'Hello' }] },
    () => undefined,
    controller.signal,
  );
  controller.abort();

  await assert.rejects(stream, (error: unknown) => {
    assert.equal(error instanceof Error && error.name === 'AbortError', true);
    return true;
  });
});

class MockXmlHttpRequest {
  public method = '';
  public url = '';
  public status = 0;
  public responseText = '';
  public body = '';
  public readonly headers = new Map<string, string>();
  public onprogress: (() => void) | null = null;
  public onload: (() => void) | null = null;
  public onerror: (() => void) | null = null;
  public onabort: (() => void) | null = null;

  public open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  public setRequestHeader(name: string, value: string): void {
    this.headers.set(name, value);
  }

  public send(body: string): void {
    this.body = body;
  }

  public abort(): void {
    this.onabort?.();
  }
}
