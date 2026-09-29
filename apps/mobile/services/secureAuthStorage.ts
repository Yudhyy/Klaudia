import * as SecureStore from 'expo-secure-store';

import type { AuthSession } from './api';
import { parseStoredAuthSession } from './authSession';

const AUTH_SESSION_KEY = 'klaudia.auth-session';
const STORAGE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

/** Load a valid bearer session and remove corrupt stored state. */
export async function loadAuthSession(): Promise<AuthSession | null> {
  const serializedSession = await SecureStore.getItemAsync(AUTH_SESSION_KEY, STORAGE_OPTIONS);
  if (serializedSession === null) return null;

  const session = parseStoredAuthSession(serializedSession);
  if (session === null) {
    await clearAuthSession();
  }
  return session;
}

/** Persist only the server-issued token and public user identity. */
export async function saveAuthSession(session: AuthSession): Promise<void> {
  await SecureStore.setItemAsync(AUTH_SESSION_KEY, JSON.stringify(session), STORAGE_OPTIONS);
}

/** Remove the local bearer session. */
export async function clearAuthSession(): Promise<void> {
  await SecureStore.deleteItemAsync(AUTH_SESSION_KEY, STORAGE_OPTIONS);
}
