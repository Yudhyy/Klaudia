import { Ionicons } from '@expo/vector-icons';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Colors, Typography } from '../../constants/theme';
import { useAuth } from '../../contexts/AuthContext';
import { useSpreadsheet } from '../../contexts/SpreadsheetContext';
import {
  api,
  type ApiAttachment,
  type ApprovalDecision,
  type PendingApproval,
  type SSEEvent,
} from '../../services/api';
import { mergeApprovals } from '../../services/ledger';
import { ApprovalCard } from '../../components/ui/ApprovalCard';
import { ChatBubble } from '../../components/ui/ChatBubble';
import { ChatInput, type Attachment } from '../../components/ui/ChatInput';

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  imageUri?: string;
  streaming?: boolean;
};

const INITIAL_MESSAGE: ChatMessage = {
  id: 'welcome',
  role: 'assistant',
  content: 'Halo. Saya siap membantu membaca dan memperbarui ledger Anda.',
};

export default function ChatScreen(): React.JSX.Element {
  const insets = useSafeAreaInsets();
  const { session } = useAuth();
  const { activeSpreadsheet, isLoading: isLoadingSpreadsheet, error: spreadsheetError } =
    useSpreadsheet();
  const [messages, setMessages] = useState<ChatMessage[]>([INITIAL_MESSAGE]);
  const [messageText, setMessageText] = useState('');
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [sessionId, setSessionId] = useState<number>();
  const [isStreaming, setIsStreaming] = useState(false);
  const [status, setStatus] = useState<string>();
  const [error, setError] = useState<string>();
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [resolvingApprovalId, setResolvingApprovalId] = useState<string>();
  const resolvingApprovalRef = useRef<string | undefined>(undefined);
  const abortController = useRef<AbortController | undefined>(undefined);
  const messageSequence = useRef(0);
  const listRef = useRef<FlatList<ChatMessage>>(null);

  const nextMessageId = useCallback((prefix: string): string => {
    messageSequence.current += 1;
    return `${prefix}-${Date.now()}-${messageSequence.current}`;
  }, []);

  useEffect(() => {
    abortController.current?.abort();
    setSessionId(undefined);
    setMessages([INITIAL_MESSAGE]);
    setApprovals([]);
    setError(undefined);
  }, [activeSpreadsheet?.spreadsheetId]);

  useEffect(
    () => () => {
      abortController.current?.abort();
    },
    [],
  );

  const sendMessage = useCallback(async (): Promise<void> => {
    const trimmedMessage = messageText.trim();
    if (
      isStreaming ||
      activeSpreadsheet === null ||
      (trimmedMessage.length === 0 && attachment === null)
    ) {
      return;
    }
    if (attachment !== null && attachment.base64 === undefined) {
      setError('Lampiran gagal dibaca. Pilih ulang file lalu coba lagi.');
      return;
    }

    const requestText = trimmedMessage || 'Tolong proses lampiran ini.';
    const sentAt = formatTimestamp(new Date().toISOString());
    const userMessage: ChatMessage = {
      id: nextMessageId('user'),
      role: 'user',
      content: requestText,
      timestamp: sentAt,
      imageUri: attachment?.type === 'image' ? attachment.uri : undefined,
    };
    const assistantMessageId = nextMessageId('assistant');
    const apiAttachment = attachment === null ? undefined : toApiAttachment(attachment);

    setMessages((current) => [
      ...current,
      userMessage,
      { id: assistantMessageId, role: 'assistant', content: '', streaming: true },
    ]);
    setMessageText('');
    setAttachment(null);
    setError(undefined);
    setIsStreaming(true);
    setStatus('Menghubungkan ke Klaudia...');

    const controller = new AbortController();
    abortController.current = controller;
    let streamedContent = '';

    try {
      await api.streamMessage(
        {
          messages: [
            {
              role: 'user',
              content: requestText,
              attachments: apiAttachment === undefined ? undefined : [apiAttachment],
            },
          ],
          session_id: sessionId,
          user_name: session?.username,
          spreadsheet_id: activeSpreadsheet.spreadsheetId,
        },
        (event) => {
          if (event.type === 'session') {
            setSessionId(event.session_id);
            return;
          }
          if (event.type === 'token') {
            streamedContent += event.text;
            updateAssistantMessage(setMessages, assistantMessageId, streamedContent, true);
            return;
          }
          if (event.type === 'approval_required') {
            setApprovals((current) => mergeApprovals(current, [event]));
            return;
          }
          if (event.type === 'done') {
            setSessionId(event.session_id);
            streamedContent = event.content || streamedContent;
            setApprovals((current) =>
              mergeApprovals(current, event.pending_approvals ?? []),
            );
            updateAssistantMessage(setMessages, assistantMessageId, streamedContent, false);
            return;
          }
          if (event.type === 'error') {
            setError(event.message || 'Klaudia gagal memproses pesan.');
            return;
          }
          setStatus(eventStatus(event));
        },
        controller.signal,
      );
    } catch (caughtError: unknown) {
      if (!isAbortError(caughtError)) {
        setError(errorMessage(caughtError, 'Tidak dapat mengirim pesan.'));
      }
    } finally {
      abortController.current = undefined;
      setIsStreaming(false);
      setStatus(undefined);
      setMessages((current) =>
        current.flatMap((message) => {
          if (message.id !== assistantMessageId) return [message];
          if (message.content.length === 0) return [];
          return [{ ...message, streaming: false }];
        }),
      );
    }
  }, [activeSpreadsheet, attachment, isStreaming, messageText, nextMessageId, session, sessionId]);

  const resolveApproval = useCallback(
    async (approvalId: string, decision: ApprovalDecision): Promise<void> => {
      if (resolvingApprovalRef.current !== undefined) return;
      resolvingApprovalRef.current = approvalId;
      setResolvingApprovalId(approvalId);
      setError(undefined);
      try {
        await api.resolveApproval(approvalId, decision);
        setApprovals((current) =>
          current.filter((approval) => approval.approval_id !== approvalId),
        );
      } catch (caughtError: unknown) {
        setError(errorMessage(caughtError, 'Gagal memproses persetujuan.'));
      } finally {
        resolvingApprovalRef.current = undefined;
        setResolvingApprovalId(undefined);
      }
    },
    [],
  );

  const ledgerUnavailable = activeSpreadsheet === null;
  const composerDisabled = isStreaming || ledgerUnavailable;

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={0}
    >
      <View style={[styles.header, { paddingTop: insets.top + 10 }]}>
        <View style={styles.headerCopy}>
          <Text style={styles.title}>Klaudia</Text>
          <Text style={styles.subtitle} numberOfLines={1}>
            {activeSpreadsheet?.name ?? 'Ledger belum tersedia'}
          </Text>
        </View>
      </View>

      {isLoadingSpreadsheet && ledgerUnavailable ? (
        <View style={styles.centerState}>
          <ActivityIndicator color={Colors.accent} />
          <Text style={styles.stateText}>Menghubungkan ke ledger...</Text>
        </View>
      ) : (
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(message) => message.id}
          renderItem={({ item }) => (
            <ChatBubble
              role={item.role}
              content={item.content}
              timestamp={item.timestamp}
              imageUri={item.imageUri}
              streaming={item.streaming}
            />
          )}
          contentContainerStyle={styles.messages}
          keyboardShouldPersistTaps="handled"
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
        />
      )}

      {status !== undefined && (
        <View style={styles.statusRow}>
          <ActivityIndicator size="small" color={Colors.accent} />
          <Text style={styles.statusText}>{status}</Text>
        </View>
      )}

      {(error ?? spreadsheetError) !== undefined && (error ?? spreadsheetError) !== null && (
        <View style={styles.errorRow}>
          <Ionicons name="alert-circle-outline" size={16} color="#FCA5A5" />
          <Text style={styles.errorText}>{error ?? spreadsheetError}</Text>
        </View>
      )}

      {approvals.map((approval) => (
        <ApprovalCard
          key={approval.approval_id}
          approval={approval}
          ledgerName={activeSpreadsheet?.name ?? 'Ledger aktif'}
          resolving={resolvingApprovalId === approval.approval_id}
          disabled={resolvingApprovalId !== undefined}
          onDecision={(approvalId, decision) => void resolveApproval(approvalId, decision)}
        />
      ))}

      <ChatInput
        value={messageText}
        onChangeText={setMessageText}
        onSend={() => void sendMessage()}
        onAttachment={setAttachment}
        onRemoveAttachment={() => setAttachment(null)}
        attachment={attachment}
        disabled={composerDisabled}
      />

    </KeyboardAvoidingView>
  );
}

function updateAssistantMessage(
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  messageId: string,
  content: string,
  streaming: boolean,
): void {
  setMessages((current) =>
    current.map((message) =>
      message.id === messageId ? { ...message, content, streaming } : message,
    ),
  );
}

function toApiAttachment(attachment: Attachment): ApiAttachment {
  return {
    filename: attachment.name,
    content_type: attachment.type === 'pdf' ? 'application/pdf' : imageContentType(attachment.name),
    data: attachment.base64 ?? '',
  };
}

function imageContentType(filename: string): string {
  const extension = filename.split('.').pop()?.toLowerCase();
  if (extension === 'png') return 'image/png';
  if (extension === 'webp') return 'image/webp';
  return 'image/jpeg';
}

function eventStatus(event: Exclude<SSEEvent, { type: 'session' | 'token' | 'approval_required' | 'done' | 'error' }>): string {
  if (event.type === 'guardrail') {
    if (event.status === 'checking') return 'Memeriksa keamanan pesan...';
    if (event.status === 'passed') return 'Pesan lolos pemeriksaan...';
    return event.message ?? 'Pesan ditolak.';
  }
  if (event.type === 'extraction') {
    if (event.status === 'processing') return `Membaca ${event.file_name ?? 'lampiran'}...`;
    return event.summary ?? event.reason ?? 'Memproses lampiran...';
  }
  if (event.type === 'tool') return `Menjalankan ${friendlyToolName(event.name)}...`;
  return `Memproses ${event.node}...`;
}

function friendlyToolName(toolName: string): string {
  if (/read|get|list|search|range/i.test(toolName)) return 'pembacaan ledger';
  if (/append|write|update|batch/i.test(toolName)) return 'perubahan ledger';
  if (/delete|clear/i.test(toolName)) return 'operasi yang perlu persetujuan';
  return 'operasi ledger';
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: Colors.border,
  },
  headerCopy: {
    flex: 1,
    marginRight: 12,
  },
  title: {
    color: Colors.textPrimary,
    fontSize: 20,
    fontWeight: '700',
  },
  subtitle: {
    marginTop: 2,
    color: Colors.textSecondary,
    fontSize: 12,
  },
  messages: {
    flexGrow: 1,
    justifyContent: 'flex-end',
    gap: 10,
    paddingVertical: 16,
  },
  centerState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
  },
  stateText: {
    ...Typography.body,
    color: Colors.textSecondary,
  },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  statusText: {
    color: Colors.textSecondary,
    fontSize: 12,
  },
  errorRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginHorizontal: 12,
    marginVertical: 4,
    padding: 10,
    borderRadius: 10,
    backgroundColor: '#2A1515',
  },
  errorText: {
    flex: 1,
    color: '#FCA5A5',
    fontSize: 12,
    lineHeight: 17,
  },
});
