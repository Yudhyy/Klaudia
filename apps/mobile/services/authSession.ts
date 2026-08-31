import type { AuthSession } from './api';

/** Parse and validate the small auth record stored on the device. */
export function parseStoredAuthSession(serializedSession: string): AuthSession | null {
  try {
    const value: unknown = JSON.parse(serializedSession);
    if (!isRecord(value)) return null;
    if (typeof value.access_token !== 'string' || value.access_token.length === 0) return null;
    if (value.token_type !== 'bearer') return null;
    if (!Number.isInteger(value.user_id) || (value.user_id as number) <= 0) return null;
    if (typeof value.username !== 'string' || value.username.length === 0) return null;

    return {
      access_token: value.access_token,
      token_type: value.token_type,
      user_id: value.user_id as number,
      username: value.username,
    };
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
