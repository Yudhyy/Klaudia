import React, { useCallback, useEffect, useRef, useState } from 'react';
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

// ─── Types ────────────────────────────────────────────────────────────────────

interface SheetTab {
  title: string;
  sheetId: number;
  index: number;
}

interface SpreadsheetInfo {
  spreadsheetId: string;
  title: string;
  sheets: SheetTab[];
}

// ─── Constants ────────────────────────────────────────────────────────────────

const BASE_URL = 'http://192.168.1.11:8000/v1';

const CELL_MIN_WIDTH = 110;
const CELL_HEIGHT = 38;
const HEADER_HEIGHT = 42;
const ROW_NUM_WIDTH = 38;

const COLORS = {
  bg: '#0F0F11',
  surface: '#18181C',
  surfaceHigh: '#222228',
  border: '#2A2A32',
  accent: '#4ADE80',
  accentDim: '#1A3D2B',
  text: '#F0F0F2',
  textMuted: '#7A7A8A',
  textDim: '#4A4A58',
  headerBg: '#141418',
  headerText: '#9696A8',
  rowEven: '#18181C',
  rowOdd: '#1C1C22',
  rowNum: '#111115',
};

// ─── API helpers ──────────────────────────────────────────────────────────────

async function fetchSpreadsheetInfo(): Promise<SpreadsheetInfo> {
  const res = await fetch(`${BASE_URL}/sheets/info`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function fetchSheetData(sheet: string): Promise<string[][]> {
  const res = await fetch(
    `${BASE_URL}/sheets/data?sheet=${encodeURIComponent(sheet)}`
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  return json.values ?? [];
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function TabPill({
  label,
  active,
  onPress,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  const scale = useRef(new Animated.Value(1)).current;

  const handlePress = () => {
    Animated.sequence([
      Animated.timing(scale, { toValue: 0.94, duration: 80, useNativeDriver: true }),
      Animated.timing(scale, { toValue: 1, duration: 80, useNativeDriver: true }),
    ]).start();
    onPress();
  };

  return (
    <Pressable onPress={handlePress}>
      <Animated.View style={[styles.tabPill, active && styles.tabPillActive, { transform: [{ scale }] }]}>
        <Text style={[styles.tabPillText, active && styles.tabPillTextActive]} numberOfLines={1}>
          {label}
        </Text>
      </Animated.View>
    </Pressable>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <View style={styles.emptyState}>
      <Text style={styles.emptyIcon}>◻</Text>
      <Text style={styles.emptyText}>{message}</Text>
    </View>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <View style={styles.emptyState}>
      <Text style={[styles.emptyIcon, { color: '#EF4444' }]}>⚠</Text>
      <Text style={[styles.emptyText, { color: '#EF4444', marginBottom: 16 }]}>{message}</Text>
      <Pressable style={styles.retryButton} onPress={onRetry}>
        <Text style={styles.retryButtonText}>Retry</Text>
      </Pressable>
    </View>
  );
}

// ─── Data Table ───────────────────────────────────────────────────────────────

function DataTable({ rows }: { rows: string[][] }) {
  if (rows.length === 0) return <EmptyState message="Sheet is empty" />;

  const numCols = Math.max(...rows.map((r) => r.length));
  const colWidths = Array.from({ length: numCols }, (_, colIdx) => {
    const maxLen = Math.max(...rows.map((r) => (r[colIdx] ?? '').length));
    return Math.max(CELL_MIN_WIDTH, Math.min(maxLen * 8 + 24, 260));
  });
  const totalWidth = ROW_NUM_WIDTH + colWidths.reduce((a, b) => a + b, 0);

  const [header, ...dataRows] = rows;

  const colLabels = colWidths.map((_, i) =>
    i < 26 ? String.fromCharCode(65 + i) : `A${String.fromCharCode(65 + (i - 26))}`
  );

  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      bounces={false}
      contentContainerStyle={{ minWidth: totalWidth }}
    >
      <View>
        <View style={[styles.colLabelRow, { width: totalWidth }]}>
          <View style={[styles.cornerCell, { width: ROW_NUM_WIDTH }]} />
          {colWidths.map((w, i) => (
            <View key={i} style={[styles.colLabelCell, { width: w }]}>
              <Text style={styles.colLabelText}>{colLabels[i]}</Text>
            </View>
          ))}
        </View>

        <View style={[styles.headerRow, { width: totalWidth }]}>
          <View style={[styles.rowNumCell, { width: ROW_NUM_WIDTH, height: HEADER_HEIGHT }]}>
            <Text style={styles.rowNumText}>1</Text>
          </View>
          {colWidths.map((w, colIdx) => (
            <View key={colIdx} style={[styles.headerCell, { width: w, height: HEADER_HEIGHT }]}>
              <Text style={styles.headerCellText} numberOfLines={1}>
                {header?.[colIdx] ?? ''}
              </Text>
            </View>
          ))}
        </View>

        <ScrollView
          showsVerticalScrollIndicator={false}
          nestedScrollEnabled
          style={{ maxHeight: 520 }}
        >
          {dataRows.map((row, rowIdx) => (
            <View
              key={rowIdx}
              style={[
                styles.dataRow,
                { width: totalWidth, backgroundColor: rowIdx % 2 === 0 ? COLORS.rowEven : COLORS.rowOdd },
              ]}
            >
              <View style={[styles.rowNumCell, { width: ROW_NUM_WIDTH }]}>
                <Text style={styles.rowNumText}>{rowIdx + 2}</Text>
              </View>
              {colWidths.map((w, colIdx) => (
                <View key={colIdx} style={[styles.dataCell, { width: w }]}>
                  <Text style={styles.dataCellText} numberOfLines={1}>
                    {row[colIdx] ?? ''}
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

// ─── Main Screen ──────────────────────────────────────────────────────────────

export default function SheetScreen() {
  const [info, setInfo] = useState<SpreadsheetInfo | null>(null);
  const [activeSheet, setActiveSheet] = useState<string | null>(null);
  const [rows, setRows] = useState<string[][]>([]);
  const [loadingInfo, setLoadingInfo] = useState(true);
  const [loadingData, setLoadingData] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fadeAnim = useRef(new Animated.Value(0)).current;

  const fadeIn = () =>
    Animated.timing(fadeAnim, { toValue: 1, duration: 280, useNativeDriver: true }).start();

  // ── Load sheet row data ───────────────────────────────────────────────────
  const loadData = useCallback(async (sheet: string) => {
    setLoadingData(true);
    fadeAnim.setValue(0);
    try {
      const data = await fetchSheetData(sheet);
      setRows(data);
      fadeIn();
    } catch (e: any) {
      setError(e.message ?? 'Failed to load sheet');
    } finally {
      setLoadingData(false);
    }
  }, []);

  // ── Load spreadsheet metadata (sheet list) ────────────────────────────────
  //
  // After fetching, we reconcile the active tab:
  //   • If the previously active sheet still exists → keep it selected
  //   • If it was deleted (or this is the first load) → select the first sheet
  //
  // `currentActive` is passed in so the callback stays pure (no stale closure).
  const loadInfo = useCallback(async (currentActive: string | null) => {
    setError(null);
    setLoadingInfo(true);
    fadeAnim.setValue(0);
    try {
      const data = await fetchSpreadsheetInfo();
      setInfo(data);

      const sheetTitles = data.sheets.map((s) => s.title);
      const stillExists = currentActive && sheetTitles.includes(currentActive);
      const targetSheet = stillExists ? currentActive : (sheetTitles[0] ?? null);

      // If the active sheet changed (new default), clear stale rows immediately
      if (targetSheet !== currentActive) {
        setRows([]);
      }

      setActiveSheet(targetSheet);
      // loadData is triggered by the activeSheet useEffect below;
      // but if targetSheet === currentActive (sheet unchanged), the effect won't
      // fire again — so we call loadData directly in that case.
      if (targetSheet && targetSheet === currentActive) {
        await loadData(targetSheet);
      }
    } catch (e: any) {
      setError(e.message ?? 'Failed to connect');
    } finally {
      setLoadingInfo(false);
    }
  }, [loadData]);

  // ── Initial load ──────────────────────────────────────────────────────────
  useEffect(() => {
    loadInfo(null);
  }, []);

  // ── Re-fetch when active tab changes ──────────────────────────────────────
  useEffect(() => {
    if (activeSheet) loadData(activeSheet);
  }, [activeSheet]);

  // ── Re-fetch everything when tab comes into focus ─────────────────────────
  //
  // useFocusEffect fires every time the user navigates TO this tab.
  // This is what makes "go to chat → Klaudia adds a sheet → come back" work.
  // We skip the very first mount (handled by the useEffect above) using a ref.
  const isFirstMount = useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (isFirstMount.current) {
        isFirstMount.current = false;
        return;
      }
      // Re-fetch meta + current data when returning to this tab
      loadInfo(activeSheet);
    }, [activeSheet, loadInfo])
  );

  // ── Pull-to-refresh ───────────────────────────────────────────────────────
  //
  // BUG FIX: was only calling loadData(), never loadInfo().
  // New sheets created by Klaudia would never appear until app restart.
  // Now: refresh = full reload (meta + data), same as tab focus.
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await loadInfo(activeSheet);
    } finally {
      setRefreshing(false);
    }
  }, [activeSheet, loadInfo]);

  const handleTabSelect = (title: string) => {
    if (title !== activeSheet) {
      setRows([]);
      setActiveSheet(title);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────

  if (loadingInfo && !info) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={COLORS.accent} />
        <Text style={styles.loadingLabel}>Connecting to spreadsheet…</Text>
      </View>
    );
  }

  if (error && !info) {
    return (
      <View style={styles.centered}>
        <ErrorState message={error} onRetry={() => loadInfo(null)} />
      </View>
    );
  }

  const dataRowCount = rows.length > 1 ? rows.length - 1 : 0;

  return (
    <View style={styles.screen}>
      {/* ── Header ── */}
      <View style={styles.header}>
        <View>
          <Text style={styles.headerLabel}>SPREADSHEET</Text>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {info?.title ?? '—'}
          </Text>
        </View>
        {activeSheet && !loadingData && (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{dataRowCount} rows</Text>
          </View>
        )}
      </View>

      {/* ── Sheet Tabs ── */}
      {info && info.sheets.length > 0 && (
        <View style={styles.tabBar}>
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.tabBarContent}
          >
            {info.sheets.map((s) => (
              <TabPill
                key={s.sheetId}
                label={s.title}
                active={s.title === activeSheet}
                onPress={() => handleTabSelect(s.title)}
              />
            ))}
          </ScrollView>
        </View>
      )}

      {/* ── Divider ── */}
      <View style={styles.divider} />

      {/* ── Table or loading ── */}
      <ScrollView
        style={styles.tableContainer}
        contentContainerStyle={styles.tableContent}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={COLORS.accent}
          />
        }
      >
        {loadingData ? (
          <View style={styles.centered}>
            <ActivityIndicator size="small" color={COLORS.accent} />
            <Text style={styles.loadingLabel}>Loading {activeSheet}…</Text>
          </View>
        ) : error ? (
          <ErrorState message={error} onRetry={() => activeSheet && loadData(activeSheet)} />
        ) : (
          <Animated.View style={{ opacity: fadeAnim }}>
            <DataTable rows={rows} />
          </Animated.View>
        )}
      </ScrollView>
    </View>
  );
}

// ─── Styles (unchanged) ───────────────────────────────────────────────────────

const styles = StyleSheet.create({
  screen:       { flex: 1, backgroundColor: COLORS.bg },
  centered:     { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.bg, gap: 12 },
  loadingLabel: { color: COLORS.textMuted, fontSize: 13, fontFamily: 'monospace' },

  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 20, paddingTop: 60, paddingBottom: 14, backgroundColor: COLORS.bg,
  },
  headerLabel: { color: COLORS.accent, fontSize: 10, fontWeight: '700', letterSpacing: 2, marginBottom: 4 },
  headerTitle: { color: COLORS.text, fontSize: 18, fontWeight: '600', maxWidth: 240 },
  badge: {
    backgroundColor: COLORS.accentDim, paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 20, borderWidth: 1, borderColor: '#2A5C3D',
  },
  badgeText: { color: COLORS.accent, fontSize: 11, fontWeight: '600' },

  tabBar:        { height: 44, backgroundColor: COLORS.bg },
  tabBarContent: { paddingHorizontal: 16, gap: 8, alignItems: 'center' },
  tabPill: {
    paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20,
    backgroundColor: COLORS.surface, borderWidth: 1, borderColor: COLORS.border,
  },
  tabPillActive:     { backgroundColor: COLORS.accentDim, borderColor: COLORS.accent },
  tabPillText:       { color: COLORS.textMuted, fontSize: 12, fontWeight: '500' },
  tabPillTextActive: { color: COLORS.accent, fontWeight: '700' },

  divider: { height: 1, backgroundColor: COLORS.border, marginTop: 8 },

  tableContainer: { flex: 1 },
  tableContent:   { flexGrow: 1, paddingBottom: 32 },

  colLabelRow:  { flexDirection: 'row', backgroundColor: COLORS.headerBg, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  cornerCell:   { height: 28, borderRightWidth: 1, borderRightColor: COLORS.border },
  colLabelCell: { height: 28, alignItems: 'center', justifyContent: 'center', borderRightWidth: 1, borderRightColor: COLORS.border },
  colLabelText: { color: COLORS.textDim, fontSize: 10, fontWeight: '600', letterSpacing: 0.5 },

  headerRow: {
    flexDirection: 'row', backgroundColor: COLORS.headerBg,
    borderBottomWidth: 2, borderBottomColor: COLORS.accent + '40',
  },
  headerCell:     { paddingHorizontal: 10, justifyContent: 'center', borderRightWidth: 1, borderRightColor: COLORS.border },
  headerCellText: { color: COLORS.accent, fontSize: 11, fontWeight: '700', letterSpacing: 0.3, textTransform: 'uppercase' },

  dataRow:    { flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: COLORS.border },
  rowNumCell: { width: ROW_NUM_WIDTH, height: CELL_HEIGHT, alignItems: 'center', justifyContent: 'center', backgroundColor: COLORS.rowNum, borderRightWidth: 1, borderRightColor: COLORS.border },
  rowNumText: { color: COLORS.textDim, fontSize: 10, fontFamily: 'monospace' },
  dataCell:   { height: CELL_HEIGHT, paddingHorizontal: 10, justifyContent: 'center', borderRightWidth: 1, borderRightColor: COLORS.border },
  dataCellText: { color: COLORS.text, fontSize: 12 },

  emptyState:      { alignItems: 'center', justifyContent: 'center', paddingVertical: 80, gap: 8 },
  emptyIcon:       { fontSize: 32, color: COLORS.textDim, marginBottom: 4 },
  emptyText:       { color: COLORS.textMuted, fontSize: 14 },
  retryButton:     { paddingHorizontal: 20, paddingVertical: 10, backgroundColor: COLORS.accentDim, borderRadius: 8, borderWidth: 1, borderColor: COLORS.accent },
  retryButtonText: { color: COLORS.accent, fontSize: 13, fontWeight: '600' },
});