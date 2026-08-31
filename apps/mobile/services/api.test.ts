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
