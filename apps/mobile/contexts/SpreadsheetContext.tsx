import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { api, type SpreadsheetSummary } from '../services/api';
import { useAuth } from './AuthContext';

type SpreadsheetContextValue = {
  activeSpreadsheet: SpreadsheetSummary | null;
  isLoading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

const SpreadsheetContext = createContext<SpreadsheetContextValue | null>(null);

/** Resolve the first ledger owned by the signed-in user. */
export function SpreadsheetProvider({ children }: PropsWithChildren): React.JSX.Element {
  const { session } = useAuth();
  const [activeSpreadsheet, setActiveSpreadsheet] = useState<SpreadsheetSummary | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshSequence = useRef(0);

  const refresh = useCallback(async (): Promise<void> => {
    const requestSequence = ++refreshSequence.current;
    if (session === null) {
      setActiveSpreadsheet(null);
      setIsLoading(false);
      setError(null);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      let spreadsheets = await api.getSpreadsheets();
      if (spreadsheets.length === 0) {
        await api.getSpreadsheetInfo();
        spreadsheets = await api.getSpreadsheets();
      }
      if (requestSequence !== refreshSequence.current) return;
      setActiveSpreadsheet(spreadsheets[0] ?? null);
    } catch (caughtError: unknown) {
      if (requestSequence !== refreshSequence.current) return;
      setActiveSpreadsheet(null);
      setError(caughtError instanceof Error ? caughtError.message : 'Failed to load the ledger.');
    } finally {
      if (requestSequence === refreshSequence.current) {
        setIsLoading(false);
      }
    }
  }, [session]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ activeSpreadsheet, isLoading, error, refresh }),
    [activeSpreadsheet, isLoading, error, refresh],
  );

  return <SpreadsheetContext.Provider value={value}>{children}</SpreadsheetContext.Provider>;
}

/** Return the ledger selected for chat and sheet reads. */
export function useSpreadsheet(): SpreadsheetContextValue {
  const context = useContext(SpreadsheetContext);
  if (context === null) {
    throw new Error('useSpreadsheet must be used within SpreadsheetProvider');
  }
  return context;
}
