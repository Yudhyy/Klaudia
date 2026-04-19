import { useState, useRef, useCallback } from 'react';
import {
  View, FlatList, KeyboardAvoidingView,
  Platform, StyleSheet, Text, Image,
  ActivityIndicator,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ChatBubble } from '../../components/ui/ChatBubble';
import { ChatInput } from '../../components/ui/ChatInput';
import { api } from '../../services/api';
import { Colors, Typography } from '../../constants/theme';
import { SessionDrawer } from '../../components/ui/SessionDrawer';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  imageUri?: string;
};

type Attachment = {
  uri: string;
  name: string;
  type: 'image' | 'pdf';
  base64?: string;
};

export default function ChatScreen() {
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '0',
      role: 'assistant',
      content: 'Halo! Saya Klaudia 👋 Upload struk belanjaan kamu dan saya akan proses otomatis.',
      timestamp: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' }),
    }
  ]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [input, setInput] = useState('');
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [sessionId, setSessionId] = useState<number | undefined>();
  const [loading, setLoading] = useState(false);
  const listRef = useRef<FlatList>(null);

  const formatTime = () =>
    new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });

  const handleSend = useCallback(async () => {
    if (!input.trim() && !attachment) return;
    if (loading) return;

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim() || (attachment ? `📎 ${attachment.name}` : ''),
      timestamp: formatTime(),
      imageUri: attachment?.type === 'image' ? attachment.uri : undefined,
    };

    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setAttachment(null);
    setLoading(true);

    try {
      const attachments = attachment?.base64 ? [{
        filename: attachment.name,
        content_type: attachment.type === 'pdf' ? 'application/pdf' : 'image/jpeg',
        data: attachment.base64,
      }] : [];

      const response = await api.sendMessage({
        messages: [{
          role: 'user',
          content: input.trim() || attachment?.name || '',
          attachments,
        }],
        session_id: sessionId,
      });

      setSessionId(response.session_id);
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: response.message.content,
        timestamp: formatTime(),
      }]);
    } catch {
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: '⚠️ Gagal menghubungi server. Coba lagi.',
        timestamp: formatTime(),
      }]);
    } finally {
      setLoading(false);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 100);
    }
  }, [input, attachment, sessionId, loading]);

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>AI Chat</Text>
      </View>

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={80}
      >
        {/* Attachment preview */}
        {attachment && (
          <View style={styles.attachPreview}>
            {attachment.type === 'image'
              ? <Image source={{ uri: attachment.uri }} style={styles.attachThumb} />
              : <Text style={styles.attachPDF}>📄 {attachment.name}</Text>
            }
          </View>
        )}

        {/* Messages */}
        <FlatList
          ref={listRef}
          style={styles.flex}
          data={messages}
          keyExtractor={item => item.id}
          renderItem={({ item }) => (
            <ChatBubble
              role={item.role}
              content={item.content}
              timestamp={item.timestamp}
              imageUri={item.imageUri}
            />
          )}
          contentContainerStyle={styles.list}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
        />

        {/* Loading */}
        {loading && (
          <View style={styles.loadingRow}>
            <ActivityIndicator size="small" color={Colors.accent} />
            <Text style={styles.loadingText}>Klaudia sedang berpikir...</Text>
          </View>
        )}

        {/* Input */}
        <ChatInput
          value={input}
          onChangeText={setInput}
          onSend={handleSend}
          onAttachment={setAttachment}
          disabled={loading}
        />
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Colors.background },
  flex: { flex: 1 },
  header: {
    paddingVertical: 16,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
    alignItems: 'center',
  },
  headerTitle: { ...Typography.header, color: Colors.textPrimary },
  list: { paddingVertical: 16 },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 20,
    paddingBottom: 8,
  },
  loadingText: { color: Colors.textSecondary, fontSize: 12 },
  attachPreview: {
    margin: 16,
    padding: 12,
    backgroundColor: Colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Colors.accent,
  },
  attachThumb: { width: 100, height: 80, borderRadius: 8 },
  attachPDF: { color: Colors.textPrimary, fontSize: 14 },
});