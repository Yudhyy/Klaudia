import { parseAuthSessionValue, type AuthSession } from './api.ts';

export { parseAuthSessionValue } from './api.ts';

/** Parse and validate the small auth record stored on the device. */
export function parseStoredAuthSession(serializedSession: string): AuthSession | null {
  try {
    const value: unknown = JSON.parse(serializedSession);
    return parseAuthSessionValue(value);
  } catch {
    return null;
  }
}
