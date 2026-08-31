import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiError,
  createApiClient,
  normalizeApiBaseUrl,
  parseSseFrames,
  type SSEEvent,
} from './api.ts';

test('normalizes a host and full API URLs', () => {
  assert.equal(normalizeApiBaseUrl('192.168.1.20'), 'http://192.168.1.20:8000/v1');
  assert.equal(normalizeApiBaseUrl('https://api.example.com'), 'https://api.example.com/v1');
  assert.equal(normalizeApiBaseUrl('https://api.example.com/v1/'), 'https://api.example.com/v1');
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

test('calls the unauthorized handler and rejects a 401 response', async () => {
  let unauthorizedCalls = 0;
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'expired-token',
    onUnauthorized: () => {
      unauthorizedCalls += 1;
    },
    fetchImpl: async () => Response.json({ detail: 'Invalid token' }, { status: 401 }),
  });

  await assert.rejects(client.getSessions(), (error: unknown) => {
    assert.equal(error instanceof ApiError, true);
    assert.equal((error as ApiError).status, 401);
    return true;
  });
  assert.equal(unauthorizedCalls, 1);
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
    `${remainder}"action":"delete_sheet","sheet":"Jun","summary":"Delete Jun","rows_affected":35,"columns_affected":7}\n\n`,
    (event) => events.push(event),
  );

  assert.equal(finalRemainder, '');
  assert.deepEqual(events[1], {
    type: 'approval_required',
    approval_id: 'approve-1',
    action: 'delete_sheet',
    sheet: 'Jun',
    summary: 'Delete Jun',
    rows_affected: 35,
    columns_affected: 7,
  });
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
  let unauthorizedCalls = 0;
  const client = createApiClient({
    baseUrl: 'http://localhost:8000/v1',
    getAccessToken: () => 'expired-token',
    onUnauthorized: () => {
      unauthorizedCalls += 1;
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
  assert.equal(unauthorizedCalls, 1);
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
