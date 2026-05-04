import { useState, useRef, useCallback } from 'react';
import {
  View, FlatList, KeyboardAvoidingView,
  Platform, StyleSheet, Text, Image,
  ActivityIndicator, TouchableOpacity,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { ChatBubble } from '../../components/ui/ChatBubble';
import { ChatInput } from '../../components/ui/ChatInput';
import { SessionDrawer } from '../../components/ui/SessionDrawer';
import { api } from '../../services/api';
import { Colors, Typography } from '../../constants/theme';

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

const INITIAL_MESSAGE: Message = {
  id: '0',
  role: 'assistant',
  content: 'Halo! Saya Klaudia 👋 Upload struk belanjaan kamu dan saya akan proses otomatis.',
  timestamp: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' }),
};

export default function ChatScreen() {
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([INITIAL_MESSAGE]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [input, setInput] = useState('');
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [sessionId, setSessionId] = useState<number | undefined>();
  const [loading, setLoading] = useState(false);
  const listRef = useRef<FlatList>(null);

  const formatTime = () =>
    new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });

  const handleSelectSession = useCallback(async (id: number) => {
    if (id === 0) {
      setSessionId(undefined);
      setMessages([{ ...INITIAL_MESSAGE, id: Date.now().toString(), timestamp: formatTime() }]);
      return;
    }
    try {
      const data = await api.getSession(id);
      setSessionId(id);
      const loaded: Message[] = data.messages.map((m: any, i: number) => ({
        id: i.toString(),
        role: m.sender,
        content: m.message_text,
        timestamp: new Date(m.timestamp).toLocaleTimeString('id-ID', {
          hour: '2-digit', minute: '2-digit',
        }),
      }));
      setMessages(loaded.length > 0 ? loaded : [INITIAL_MESSAGE]);
      setTimeout(() => listRef.current?.scrollToEnd({ animated: false }), 100);
    } catch {
      // keep current
    }
  }, []);

  const handleSend = useCallback(async () => {
    if (!input.trim() && !attachment) return;
    if (loading) return;

    const userMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim() || `📎 ${attachment?.name}`,
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
        <View style={styles.headerSide} />
        <Text style={styles.headerTitle}>AI Chat</Text>
        <TouchableOpacity
          style={styles.headerSide}
          onPress={() => setDrawerOpen(true)}
        >
          <Ionicons name="menu" size={22} color={Colors.textPrimary} />
        </TouchableOpacity>
      </View>

      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={80}
      >
        {/* Attachment preview */}
        {attachment && (
          <View style={styles.attachPreview}>
            <View style={styles.attachRow}>
              {attachment.type === 'image'
                ? <Image source={{ uri: attachment.uri }} style={styles.attachThumb} />
                : <Text style={styles.attachPDF}>📄 {attachment.name}</Text>
              }
              <TouchableOpacity
                onPress={() => setAttachment(null)}
                style={styles.attachClose}
              >
                <Ionicons name="close-circle" size={18} color={Colors.textSecondary} />
              </TouchableOpacity>
            </View>
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

      {/* Session Drawer */}
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
  container: { flex: 1, backgroundColor: Colors.background },
  flex: { flex: 1 },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 16,
    paddingHorizontal: 20,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  headerSide: {
    width: 32,
    alignItems: 'flex-end',
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
    marginHorizontal: 16,
    marginTop: 8,
    padding: 10,
    backgroundColor: Colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: Colors.accent,
  },
  attachRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  attachThumb: { width: 80, height: 60, borderRadius: 8 },
  attachPDF: { color: Colors.textPrimary, fontSize: 13, flex: 1 },
  attachClose: { padding: 4 },
});