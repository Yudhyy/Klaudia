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
  content: 'Hello. I am ready to help you read and update your ledger.',
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
  const [taskId, setTaskId] = useState<string>();
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
    setTaskId(undefined);
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
      setError('Failed to read the attachment. Select the file again and try again.');
      return;
    }

    const requestText = trimmedMessage || 'Please process this attachment.';
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
    setStatus('Connecting to Klaudia...');

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
            setStatus('Preparing the approval...');
            return;
          }
          if (event.type === 'done') {
            setSessionId(event.session_id);
            setTaskId(event.task_id ?? undefined);
            streamedContent = event.content || streamedContent;
            if (
              event.run_status !== null &&
              event.run_status !== undefined &&
              event.run_status !== 'answered' &&
              event.run_status !== 'awaiting_approval'
            ) {
              setError(`Klaudia stopped with status: ${event.run_status}. The ledger may have changed.`);
            }
            setApprovals((current) =>
              mergeApprovals(current, event.pending_approvals ?? []),
            );
            updateAssistantMessage(setMessages, assistantMessageId, streamedContent, false);
            return;
          }
          if (event.type === 'error') {
            setError(event.message || 'Klaudia failed to process the message.');
            return;
          }
          setStatus(eventStatus(event));
        },
        controller.signal,
      );
    } catch (caughtError: unknown) {
      if (!isAbortError(caughtError)) {
        setError(errorMessage(caughtError, 'Unable to send the message.'));
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
        if (taskId === undefined) {
          throw new Error('The saved task is unavailable. Reload the conversation before retrying.');
        }
        await api.resolveApproval(approvalId, decision);
        const resumed = await api.resumeTask(taskId);
        if (resumed.run_status !== 'answered' && resumed.run_status !== 'awaiting_approval') {
          throw new Error(`The saved task stopped with status: ${resumed.run_status}. Retry the decision to resume it.`);
        }
        setSessionId(resumed.session_id);
        setTaskId(resumed.task_id ?? undefined);
        setApprovals((current) =>
          mergeApprovals(
            current.filter((approval) => approval.approval_id !== approvalId),
            resumed.pending_approvals,
          ),
        );
        if (resumed.message.content) {
          setMessages((current) => [
            ...current,
            {
              id: nextMessageId('assistant'),
              role: 'assistant',
              content: resumed.message.content,
            },
          ]);
        }
      } catch (caughtError: unknown) {
        setError(errorMessage(caughtError, 'Failed to process the approval.'));
      } finally {
        resolvingApprovalRef.current = undefined;
        setResolvingApprovalId(undefined);
      }
    },
    [nextMessageId, taskId],
  );

  const ledgerUnavailable = activeSpreadsheet === null;
  const composerDisabled = isStreaming || ledgerUnavailable || approvals.length > 0;

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
            {activeSpreadsheet?.name ?? 'Ledger not available'}
          </Text>
        </View>
      </View>

      {isLoadingSpreadsheet && ledgerUnavailable ? (
        <View style={styles.centerState}>
          <ActivityIndicator color={Colors.accent} />
          <Text style={styles.stateText}>Connecting to the ledger...</Text>
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
          ledgerName={activeSpreadsheet?.name ?? 'Active ledger'}
          resolving={resolvingApprovalId === approval.approval_id}
          disabled={resolvingApprovalId !== undefined || isStreaming || taskId === undefined}
          onDecision={(approvalId, decision) => void resolveApproval(approvalId, decision)}
        />
      ))}

      {approvals.length > 0 && (
        <Text style={styles.pendingNote}>Review the pending change before sending another message.</Text>
      )}

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
    if (event.status === 'checking') return 'Checking message safety...';
    if (event.status === 'passed') return 'Message passed safety checks...';
    return event.message ?? 'Message rejected.';
  }
  if (event.type === 'extraction') {
    if (event.status === 'processing') return `Reading ${event.file_name ?? 'attachment'}...`;
    return event.summary ?? event.reason ?? 'Processing attachment...';
  }
  if (event.type === 'tool') return `Running ${friendlyToolName(event.name)}...`;
  return `Processing ${event.node}...`;
}

function friendlyToolName(toolName: string): string {
  if (/read|get|list|search|range/i.test(toolName)) return 'a ledger read operation';
  if (/append|write|update|batch/i.test(toolName)) return 'a ledger update operation';
  if (/delete|clear/i.test(toolName)) return 'an operation that requires approval';
  return 'a ledger operation';
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
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
  pendingNote: {
    color: Colors.textSecondary,
    fontSize: 12,
    paddingHorizontal: 16,
    paddingBottom: 8,
  },
});
