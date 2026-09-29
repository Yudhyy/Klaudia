import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useFocusEffect } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Colors } from '../../constants/theme';
import { useSpreadsheet } from '../../contexts/SpreadsheetContext';
import { api, type JsonScalar, type SpreadsheetInfo } from '../../services/api';
import { formatCellValue } from '../../services/ledger';

const CELL_MIN_WIDTH = 110;
const CELL_HEIGHT = 38;
const HEADER_HEIGHT = 42;
const ROW_NUMBER_WIDTH = 38;

type TabButtonProps = {
  label: string;
  active: boolean;
  onPress: () => void;
};

function TabButton({ label, active, onPress }: TabButtonProps): React.JSX.Element {
  return (
    <Pressable
      style={({ pressed }) => [
        styles.tabButton,
        active && styles.tabButtonActive,
        pressed && styles.pressed,
      ]}
      onPress={onPress}
    >
      <Text style={[styles.tabText, active && styles.tabTextActive]} numberOfLines={1}>
        {label}
      </Text>
    </Pressable>
  );
}

type ErrorStateProps = {
  message: string;
  onRetry: () => void;
};

function ErrorState({ message, onRetry }: ErrorStateProps): React.JSX.Element {
  return (
    <View style={styles.stateContainer}>
      <Text style={styles.errorText}>{message}</Text>
      <Pressable style={styles.retryButton} onPress={onRetry}>
        <Text style={styles.retryText}>Try Again</Text>
      </Pressable>
    </View>
  );
}

function DataTable({ rows }: { rows: JsonScalar[][] }): React.JSX.Element {
  if (rows.length === 0) {
    return (
      <View style={styles.stateContainer}>
        <Text style={styles.stateText}>This sheet is empty.</Text>
      </View>
    );
  }

  const columnCount = Math.max(...rows.map((row) => row.length));
  const columnWidths = Array.from({ length: columnCount }, (_, columnIndex) => {
    const longestCell = Math.max(
      ...rows.map((row) => formatCellValue(row[columnIndex]).length),
    );
    return Math.max(CELL_MIN_WIDTH, Math.min(longestCell * 8 + 24, 260));
  });
  const tableWidth =
    ROW_NUMBER_WIDTH + columnWidths.reduce((total, width) => total + width, 0);
  const [header, ...bodyRows] = rows;

  return (
    <ScrollView
      horizontal
      bounces={false}
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={{ minWidth: tableWidth }}
    >
      <View>
        <View style={[styles.columnLabelRow, { width: tableWidth }]}>
          <View style={[styles.cornerCell, { width: ROW_NUMBER_WIDTH }]} />
          {columnWidths.map((width, columnIndex) => (
            <View key={columnIndex} style={[styles.columnLabelCell, { width }]}>
              <Text style={styles.columnLabelText}>{columnLabel(columnIndex)}</Text>
            </View>
          ))}
        </View>

        <View style={[styles.headerRow, { width: tableWidth }]}>
          <RowNumber value={1} height={HEADER_HEIGHT} />
          {columnWidths.map((width, columnIndex) => (
            <View
              key={columnIndex}
              style={[styles.headerCell, { width, height: HEADER_HEIGHT }]}
            >
              <Text style={styles.headerCellText} numberOfLines={1}>
                {formatCellValue(header[columnIndex])}
              </Text>
            </View>
          ))}
        </View>

        <ScrollView nestedScrollEnabled style={styles.bodyScroll}>
          {bodyRows.map((row, rowIndex) => (
            <View
              key={rowIndex}
              style={[
                styles.dataRow,
                styles[rowIndex % 2 === 0 ? 'evenRow' : 'oddRow'],
                { width: tableWidth },
              ]}
            >
              <RowNumber value={rowIndex + 2} height={CELL_HEIGHT} />
              {columnWidths.map((width, columnIndex) => (
                <View key={columnIndex} style={[styles.dataCell, { width }]}>
                  <Text style={styles.dataCellText} numberOfLines={1}>
                    {formatCellValue(row[columnIndex])}
                  </Text>
                </View>
              ))}
            </View>
          ))}
        </ScrollView>
      </View>
    </ScrollView>
  );
}

function RowNumber({ value, height }: { value: number; height: number }): React.JSX.Element {
  return (
    <View style={[styles.rowNumberCell, { width: ROW_NUMBER_WIDTH, height }]}>
      <Text style={styles.rowNumberText}>{value}</Text>
    </View>
  );
}

export default function SheetScreen(): React.JSX.Element {
  const insets = useSafeAreaInsets();
  const {
    activeSpreadsheet,
    isLoading: isLoadingSpreadsheets,
    error: spreadsheetError,
    refresh: refreshSpreadsheets,
  } = useSpreadsheet();
  const [info, setInfo] = useState<SpreadsheetInfo | null>(null);
  const [activeSheet, setActiveSheet] = useState<string | null>(null);
  const [rows, setRows] = useState<JsonScalar[][]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const opacity = useRef(new Animated.Value(0)).current;
  const didFocusOnce = useRef(false);
  const activeSheetRef = useRef<string | null>(null);
  const requestSequence = useRef(0);

  const loadSheet = useCallback(
    async (
      sheetName: string,
      existingRequestSequence?: number,
    ): Promise<void> => {
      if (activeSpreadsheet === null) return;
      const currentRequestSequence =
        existingRequestSequence ?? ++requestSequence.current;
      setIsLoading(true);
      setError(null);
      opacity.setValue(0);
      try {
        const nextRows = await api.getSheetData(
          sheetName,
          activeSpreadsheet.spreadsheetId,
        );
        if (currentRequestSequence !== requestSequence.current) return;
        setRows(nextRows);
        Animated.timing(opacity, {
          toValue: 1,
          duration: 180,
          useNativeDriver: true,
        }).start();
      } catch (caughtError: unknown) {
        if (currentRequestSequence !== requestSequence.current) return;
        setError(errorMessage(caughtError, 'Failed to load the sheet data.'));
      } finally {
        if (currentRequestSequence === requestSequence.current) {
          setIsLoading(false);
        }
      }
    },
    [activeSpreadsheet, opacity],
  );

  const loadLedger = useCallback(
    async (preferredSheet?: string | null): Promise<void> => {
      const currentRequestSequence = ++requestSequence.current;
      if (activeSpreadsheet === null) {
        setInfo(null);
        setActiveSheet(null);
        setRows([]);
        return;
      }

      setIsLoading(true);
      setError(null);
      try {
        const nextInfo = await api.getSpreadsheetInfo(activeSpreadsheet.spreadsheetId);
        if (currentRequestSequence !== requestSequence.current) return;
        setInfo(nextInfo);
        const sheetTitles = nextInfo.sheets.map((sheet) => sheet.title);
        const nextSheet =
          preferredSheet !== null &&
          preferredSheet !== undefined &&
          sheetTitles.includes(preferredSheet)
            ? preferredSheet
            : sheetTitles[0] ?? null;
        activeSheetRef.current = nextSheet;
        setActiveSheet(nextSheet);
        if (nextSheet === null) {
          setRows([]);
        } else {
          await loadSheet(nextSheet, currentRequestSequence);
        }
      } catch (caughtError: unknown) {
        if (currentRequestSequence !== requestSequence.current) return;
        setError(errorMessage(caughtError, 'Failed to load the ledger.'));
      } finally {
        if (currentRequestSequence === requestSequence.current) {
          setIsLoading(false);
        }
      }
    },
    [activeSpreadsheet, loadSheet],
  );

  useEffect(() => {
    void loadLedger();
    return () => {
      requestSequence.current += 1;
    };
  }, [loadLedger]);

  useFocusEffect(
    useCallback(() => {
      if (!didFocusOnce.current) {
        didFocusOnce.current = true;
        return;
      }
      void loadLedger(activeSheetRef.current);
    }, [loadLedger]),
  );

  const refresh = useCallback(async (): Promise<void> => {
    setIsRefreshing(true);
    await refreshSpreadsheets();
    await loadLedger(activeSheetRef.current);
    setIsRefreshing(false);
  }, [loadLedger, refreshSpreadsheets]);

  const selectSheet = (sheetName: string): void => {
    if (sheetName === activeSheet) return;
    activeSheetRef.current = sheetName;
    setActiveSheet(sheetName);
    setRows([]);
    void loadSheet(sheetName);
  };

  if (isLoadingSpreadsheets && activeSpreadsheet === null) {
    return <LoadingState label="Connecting to the ledger..." />;
  }

  if (activeSpreadsheet === null) {
    return (
      <ErrorState
        message={spreadsheetError ?? 'This account does not have a ledger.'}
        onRetry={() => void refreshSpreadsheets()}
      />
    );
  }

  const dataRowCount = Math.max(rows.length - 1, 0);

  return (
    <View style={styles.screen}>
      <View style={[styles.pageHeader, { paddingTop: insets.top + 20 }]}>
        <View style={styles.headerText}>
          <Text style={styles.headerLabel}>LEDGER</Text>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {activeSpreadsheet.name}
          </Text>
        </View>
        {activeSheet !== null && !isLoading && (
          <View style={styles.rowBadge}>
            <Text style={styles.rowBadgeText}>{dataRowCount} rows</Text>
          </View>
        )}
      </View>

      {info !== null && info.sheets.length > 0 && (
        <ScrollView
          horizontal
          style={styles.tabBar}
          contentContainerStyle={styles.tabBarContent}
          showsHorizontalScrollIndicator={false}
        >
          {info.sheets.map((sheet) => (
            <TabButton
              key={sheet.sheetId}
              label={sheet.title}
              active={sheet.title === activeSheet}
              onPress={() => selectSheet(sheet.title)}
            />
          ))}
        </ScrollView>
      )}

      <View style={styles.divider} />

      <ScrollView
        style={styles.tableContainer}
        contentContainerStyle={styles.tableContent}
        refreshControl={
          <RefreshControl
            refreshing={isRefreshing}
            onRefresh={() => void refresh()}
            tintColor={Colors.accent}
          />
        }
      >
        {isLoading ? (
          <LoadingState label={activeSheet === null ? 'Loading ledger...' : `Loading ${activeSheet}...`} />
        ) : error !== null ? (
          <ErrorState message={error} onRetry={() => void loadLedger(activeSheet)} />
        ) : info?.sheets.length === 0 ? (
          <View style={styles.stateContainer}>
            <Text style={styles.stateText}>This ledger does not contain any sheets.</Text>
          </View>
        ) : (
          <Animated.View style={{ opacity }}>
            <DataTable rows={rows} />
          </Animated.View>
        )}
      </ScrollView>
    </View>
  );
}

function LoadingState({ label }: { label: string }): React.JSX.Element {
  return (
    <View style={styles.loadingContainer}>
      <ActivityIndicator color={Colors.accent} />
      <Text style={styles.stateText}>{label}</Text>
    </View>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function columnLabel(columnIndex: number): string {
  let remaining = columnIndex + 1;
  let label = '';
  while (remaining > 0) {
    const characterIndex = (remaining - 1) % 26;
    label = String.fromCharCode(65 + characterIndex) + label;
    remaining = Math.floor((remaining - 1) / 26);
  }
  return label;
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  pageHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingBottom: 14,
  },
  headerText: {
    flex: 1,
    paddingRight: 16,
  },
  headerLabel: {
    marginBottom: 4,
    color: Colors.accent,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 2,
  },
  headerTitle: {
    color: Colors.textPrimary,
    fontSize: 18,
    fontWeight: '600',
  },
  rowBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderWidth: 1,
    borderColor: '#536600',
    borderRadius: 20,
    backgroundColor: '#293300',
  },
  rowBadgeText: {
    color: Colors.accent,
    fontSize: 11,
    fontWeight: '600',
  },
  tabBar: {
    maxHeight: 44,
  },
  tabBarContent: {
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 16,
  },
  tabButton: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 20,
    backgroundColor: Colors.surface,
  },
  tabButtonActive: {
    borderColor: Colors.accent,
    backgroundColor: '#293300',
  },
  tabText: {
    color: Colors.textSecondary,
    fontSize: 12,
    fontWeight: '500',
  },
  tabTextActive: {
    color: Colors.accent,
    fontWeight: '700',
  },
  pressed: {
    opacity: 0.7,
  },
  divider: {
    height: 1,
    marginTop: 8,
    backgroundColor: Colors.border,
  },
  tableContainer: {
    flex: 1,
  },
  tableContent: {
    flexGrow: 1,
    paddingBottom: 32,
  },
  loadingContainer: {
    flex: 1,
    minHeight: 240,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    backgroundColor: Colors.background,
  },
  stateContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    paddingHorizontal: 24,
    paddingVertical: 80,
    backgroundColor: Colors.background,
  },
  stateText: {
    color: Colors.textSecondary,
    fontSize: 14,
    textAlign: 'center',
  },
  errorText: {
    color: '#F87171',
    fontSize: 14,
    textAlign: 'center',
  },
  retryButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: 8,
    backgroundColor: '#293300',
  },
  retryText: {
    color: Colors.accent,
    fontSize: 13,
    fontWeight: '600',
  },
  columnLabelRow: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
    backgroundColor: '#141418',
  },
  cornerCell: {
    height: 28,
    borderRightWidth: 1,
    borderRightColor: Colors.border,
  },
  columnLabelCell: {
    height: 28,
    alignItems: 'center',
    justifyContent: 'center',
    borderRightWidth: 1,
    borderRightColor: Colors.border,
  },
  columnLabelText: {
    color: '#5F5F6E',
    fontSize: 10,
    fontWeight: '600',
  },
  headerRow: {
    flexDirection: 'row',
    borderBottomWidth: 2,
    borderBottomColor: '#536600',
    backgroundColor: '#141418',
  },
  headerCell: {
    justifyContent: 'center',
    paddingHorizontal: 10,
    borderRightWidth: 1,
    borderRightColor: Colors.border,
  },
  headerCellText: {
    color: Colors.accent,
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  bodyScroll: {
    maxHeight: 520,
  },
  dataRow: {
    flexDirection: 'row',
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  evenRow: {
    backgroundColor: '#18181C',
  },
  oddRow: {
    backgroundColor: '#1C1C22',
  },
  rowNumberCell: {
    alignItems: 'center',
    justifyContent: 'center',
    borderRightWidth: 1,
    borderRightColor: Colors.border,
    backgroundColor: '#111115',
  },
  rowNumberText: {
    color: '#5F5F6E',
    fontSize: 10,
    fontVariant: ['tabular-nums'],
  },
  dataCell: {
    height: CELL_HEIGHT,
    justifyContent: 'center',
    paddingHorizontal: 10,
    borderRightWidth: 1,
    borderRightColor: Colors.border,
  },
  dataCellText: {
    color: Colors.textPrimary,
    fontSize: 12,
  },
});
