import assert from 'node:assert/strict';
import test from 'node:test';

import { parseAuthSessionValue, parseStoredAuthSession } from './authSession.ts';

test('restores the safe auth fields from secure storage', () => {
  const session = parseStoredAuthSession(
    JSON.stringify({
      access_token: 'signed-token',
      token_type: 'bearer',
      user_id: 42,
      username: 'klaudia-user',
      password: 'must-not-survive',
    }),
  );

  assert.deepEqual(session, {
    access_token: 'signed-token',
    token_type: 'bearer',
    user_id: 42,
    username: 'klaudia-user',
  });
});

test('rejects corrupt and incomplete stored sessions', () => {
  assert.equal(parseStoredAuthSession('not-json'), null);
  assert.equal(
    parseStoredAuthSession(
      JSON.stringify({ access_token: 'token', token_type: 'bearer', username: 'missing-id' }),
    ),
    null,
  );
});

test('validates an auth response before it can be stored', () => {
  assert.deepEqual(
    parseAuthSessionValue({
      access_token: 'jwt-token',
      token_type: 'bearer',
      user_id: 8,
      username: 'klaudia-user',
    }),
    {
      access_token: 'jwt-token',
      token_type: 'bearer',
      user_id: 8,
      username: 'klaudia-user',
    },
  );
  assert.equal(
    parseAuthSessionValue({
      access_token: 'jwt-token',
      token_type: 'bearer',
      user_id: '8',
      username: 'klaudia-user',
    }),
    null,
  );
});
