// app/(tabs)/index.tsx
//
// Keyboard approach:
//   iOS  — KeyboardAvoidingView behavior='padding' with measured offset (reliable)
//   Android — app.json sets windowSoftInputMode=adjustResize so the OS shrinks
//             the window; we just need flex:1 and NO KAV interference
//
import { useState, useRef, useCallback, useEffect } from 'react';
import {
  View, FlatList, KeyboardAvoidingView, Platform,
  StyleSheet, Text, TouchableOpacity, Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { ChatBubble } from '../../components/ui/ChatBubble';
import { ChatInput, type Attachment } from '../../components/ui/ChatInput';
import { SessionDrawer } from '../../components/ui/SessionDrawer';
import { streamMessage, getSession, type SSEEvent } from '../../services/api';
import { Colors, Typography } from '../../constants/theme';

// ─── Types ────────────────────────────────────────────────────────────────────

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  imageUri?: string;
  streaming?: boolean;
};

type StatusLine = { text: string } | null;

// ─── Pipeline status → friendly label ────────────────────────────────────────

const GSHEETS_READ  = new Set(['list_sheets','get_sheet_formulas','get_sheet_data','get_spreadsheet_info','get_multiple_sheet_data']);
const GSHEETS_WRITE = new Set(['update_cells','batch_update_cells','append_rows','add_rows','add_columns']);
const GSHEETS_SHEET = new Set(['create_sheet','rename_sheet','copy_sheet','batch_update']);
const GSHEETS_CLEAR = new Set(['clear_range','delete_sheet']);
const SQLITE_OPS    = new Set(['get_document','list_documents','create_document','update_document_status','get_extraction','save_extraction','get_session_files','list_pages','get_page','create_page','update_page']);

function eventToStatus(event: SSEEvent): StatusLine {
  switch (event.type) {
    case 'guardrail': return null;
    case 'extraction': {
      const name = event.file_name ?? 'file';
      switch (event.status) {
        case 'processing': return { text: `Extracting ${name}...` };
        case 'queueing':   return { text: `Queueing ${name}...` };
        case 'queued':     return { text: `${name} queued...` };
        case 'page_done':  return { text: 'Processing page...' };
        case 'rejected':   return { text: event.reason ?? 'File could not be processed' };
        default:           return null;
      }
    }
    case 'step': {
      switch (event.node) {
        case 'sql_agent':       return { text: 'Searching transactions...' };
        case 'data_entry_team': return { text: 'Updating spreadsheet...' };
        default:                return null;
      }
    }
    case 'tool': {
      const t = event.name;
      if (GSHEETS_READ.has(t))  return { text: 'Reading spreadsheet...' };
      if (GSHEETS_WRITE.has(t)) return { text: 'Writing to spreadsheet...' };
      if (GSHEETS_SHEET.has(t)) return { text: 'Organizing sheets...' };
      if (GSHEETS_CLEAR.has(t)) return { text: 'Clearing data...' };
      if (SQLITE_OPS.has(t))    return { text: 'Reading saved receipts...' };
      return { text: 'Processing...' };
    }
    default: return null;
  }
}

// ─── StatusStrip ─────────────────────────────────────────────────────────────

function StatusStrip({ status }: { status: StatusLine }) {
  const opacity = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(opacity, { toValue: status ? 1 : 0, duration: 180, useNativeDriver: true }).start();
  }, [status]);
  return (
    <Animated.View style={[strip.container, { opacity }]} pointerEvents="none">
      <Text style={strip.text} numberOfLines={1}>{status?.text ?? ''}</Text>
    </Animated.View>
  );
}
const strip = StyleSheet.create({
  container: {
    paddingHorizontal: 16,
    paddingVertical: 4,
    backgroundColor: 'rgba(204,255,0,0.04)',
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: 'rgba(204,255,0,0.15)',
  },
  text: { fontSize: 11, color: Colors.textSecondary, fontStyle: 'italic' },
});

// ─── Helpers ─────────────────────────────────────────────────────────────────

const INITIAL_MESSAGE: Message = {
  id: '0',
  role: 'assistant',
  content: 'Hello! I am Klaudia. Upload your receipts or describe your transactions, and I will record them with precision.',
  timestamp: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' }),
};

function formatTime() {
  return new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
}

// ─── Screen ──────────────────────────────────────────────────────────────────

export default function ChatScreen() {
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([INITIAL_MESSAGE]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [input, setInput] = useState('');
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [sessionId, setSessionId] = useState<number | undefined>();
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<StatusLine>(null);
  const [headerHeight, setHeaderHeight] = useState(52);

  const listRef = useRef<FlatList>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => { return () => { abortRef.current?.abort(); }; }, []);

  const scrollToBottom = useCallback((animated = true) => {
    setTimeout(() => listRef.current?.scrollToEnd({ animated }), 50);
  }, []);

  const handleSelectSession = useCallback(async (id: number) => {
    if (id === 0) {
      setSessionId(undefined);
      setMessages([{ ...INITIAL_MESSAGE, id: Date.now().toString(), timestamp: formatTime() }]);
      return;
    }
    try {
      const data = await getSession(id);
      setSessionId(id);
      const loaded: Message[] = data.messages.map((m: any, i: number) => ({
        id: i.toString(),
        role: m.sender,
        content: m.message_text,
        timestamp: new Date(m.timestamp).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' }),
      }));
      setMessages(loaded.length > 0 ? loaded : [INITIAL_MESSAGE]);
      scrollToBottom(false);
    } catch { /* keep current */ }
  }, [scrollToBottom]);

  const handleSend = useCallback(async () => {
    if ((!input.trim() && !attachment) || loading) return;

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim(),
      timestamp: formatTime(),
      imageUri: attachment?.type === 'image' ? attachment.uri : undefined,
    };
    setMessages(prev => [...prev, userMsg]);

    const sentInput = input.trim();
    const sentAttachment = attachment;
    setInput('');
    setAttachment(null);
    setLoading(true);
    scrollToBottom();

    const streamingId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: streamingId, role: 'assistant', content: '', timestamp: formatTime(), streaming: true }]);
    scrollToBottom();

    const attachments = sentAttachment?.base64 ? [{
      filename: sentAttachment.name,
      content_type: sentAttachment.type === 'pdf' ? 'application/pdf' : 'image/jpeg',
      data: sentAttachment.base64,
    }] : [];

    const payload = {
      messages: [{ role: 'user' as const, content: sentInput || sentAttachment?.name || '', attachments }],
      session_id: sessionId,
    };

    abortRef.current = new AbortController();
    try {
      await streamMessage(payload, (event: SSEEvent) => {
        const newStatus = eventToStatus(event);
        if (newStatus !== undefined) setStatus(newStatus);
        switch (event.type) {
          case 'session': setSessionId(event.session_id); break;
          case 'token':
            setMessages(prev => prev.map(m => m.id === streamingId ? { ...m, content: m.content + event.text } : m));
            scrollToBottom();
            break;
          case 'done':
            setMessages(prev => prev.map(m => m.id === streamingId ? { ...m, content: event.content, streaming: false, timestamp: formatTime() } : m));
            setStatus(null); setLoading(false); scrollToBottom();
            break;
          case 'error':
            setMessages(prev => prev.map(m => m.id === streamingId ? { ...m, content: `Error: ${event.message}`, streaming: false, timestamp: formatTime() } : m));
            setStatus(null); setLoading(false);
            break;
        }
      }, abortRef.current.signal);
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      setMessages(prev => prev.map(m => m.id === streamingId ? { ...m, content: 'Failed to reach server. Try again.', streaming: false } : m));
      setStatus(null); setLoading(false);
    }
  }, [input, attachment, sessionId, loading, scrollToBottom]);

  // ── iOS: KAV offset = insets.top (status bar) + measured header height
  // ── Android: adjustResize in app.json handles it — KAV is a passthrough
  const kavOffset = insets.top + headerHeight;
  // Bottom padding: iOS needs home indicator clearance, Android gesture bar is thinner
  // const bottomPad = Platform.OS === 'ios' ? insets.bottom : Math.max(insets.bottom, 8);
  const bottomPad = 8;

  return (
    <View style={[styles.root, { paddingTop: insets.top }]}>
      {/* ── Header ── */}
      <View
        style={styles.header}
        onLayout={e => setHeaderHeight(e.nativeEvent.layout.height)}
      >
        <View style={styles.headerSide} />
        <Text style={styles.headerTitle}>AI Chat</Text>
        <TouchableOpacity
          style={styles.headerBtn}
          onPress={() => setDrawerOpen(true)}
          hitSlop={12}
        >
          <Ionicons name="menu" size={22} color={Colors.textPrimary} />
        </TouchableOpacity>
      </View>

      {/* ── Content area ── */}
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={kavOffset}
      >
        <FlatList
          ref={listRef}
          style={styles.flex}
          data={messages}
          keyExtractor={item => item.id}
          renderItem={({ item }) => (
            <ChatBubble
              role={item.role}
              content={item.content}
              timestamp={item.streaming ? undefined : item.timestamp}
              imageUri={item.imageUri}
              streaming={item.streaming}
            />
          )}
          contentContainerStyle={styles.list}
          keyboardShouldPersistTaps="handled"
        />

        <StatusStrip status={status} />

        {/* Input pinned above keyboard — bottom padding accounts for home indicator */}
        <View style={{ paddingBottom: bottomPad }}>
          <ChatInput
            value={input}
            onChangeText={setInput}
            onSend={handleSend}
            onAttachment={setAttachment}
            onRemoveAttachment={() => setAttachment(null)}
            attachment={attachment}
            disabled={loading}
          />
        </View>
      </KeyboardAvoidingView>

      <SessionDrawer
        visible={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onSelectSession={handleSelectSession}
        currentSessionId={sessionId}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root:       { flex: 1, backgroundColor: Colors.background },
  flex:       { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 14,
    paddingHorizontal: 20,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: Colors.border,
  },
  headerSide:  { width: 44, alignItems: 'flex-end' },
  headerBtn:   { width: 44, height: 44, alignItems: 'flex-end', justifyContent: 'center' },
  headerTitle: { ...Typography.header, color: Colors.textPrimary },
  list:        { paddingVertical: 12 },
});