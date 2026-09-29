import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

import {
  api,
  setApiAccessToken,
  setUnauthorizedHandler,
  type AuthSession,
  type LoginInput,
  type RegisterInput,
} from '../services/api';
import {
  clearAuthSession,
  loadAuthSession,
  saveAuthSession,
} from '../services/secureAuthStorage';

type AuthContextValue = {
  session: AuthSession | null;
  isRestoring: boolean;
  login: (input: LoginInput) => Promise<void>;
  register: (input: RegisterInput) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

/** Own the device auth session and synchronize it with the API client. */
export function AuthProvider({ children }: PropsWithChildren): React.JSX.Element {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [isRestoring, setIsRestoring] = useState(true);

  useEffect(() => {
    let mounted = true;
    void loadAuthSession()
      .then((storedSession) => {
        if (!mounted) return;
        setApiAccessToken(storedSession?.access_token ?? null);
        setSession(storedSession);
      })
      .catch(() => {
        if (!mounted) return;
        setApiAccessToken(null);
        setSession(null);
      })
      .finally(() => {
        if (mounted) setIsRestoring(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const installSession = useCallback(async (nextSession: AuthSession): Promise<void> => {
    await saveAuthSession(nextSession);
    setApiAccessToken(nextSession.access_token);
    setSession(nextSession);
  }, []);

  const clearSessionState = useCallback((): void => {
    setApiAccessToken(null);
    setSession(null);
  }, []);

  const logout = useCallback(async (): Promise<void> => {
    await clearAuthSession();
    clearSessionState();
  }, [clearSessionState]);

  useEffect(() => {
    setUnauthorizedHandler((rejectedToken) => {
      if (rejectedToken !== session?.access_token) return;
      void clearAuthSession().catch(() => undefined).finally(clearSessionState);
    });
    return () => setUnauthorizedHandler();
  }, [clearSessionState, session?.access_token]);

  const login = useCallback(
    async (input: LoginInput): Promise<void> => {
      await installSession(await api.login(input));
    },
    [installSession],
  );

  const register = useCallback(
    async (input: RegisterInput): Promise<void> => {
      await installSession(await api.register(input));
    },
    [installSession],
  );

  const value = useMemo(
    () => ({ session, isRestoring, login, register, logout }),
    [session, isRestoring, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Return the active auth session owned by AuthProvider. */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
