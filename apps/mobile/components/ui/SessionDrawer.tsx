import { useEffect, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, FlatList,
  StyleSheet, Animated, Pressable, ActivityIndicator,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Colors, Typography } from '../../constants/theme';
import { api } from '../../services/api';

type Session = {
  session_id: number;
  session_name: string | null;
  created_at: string;
  updated_at: string;
};

type Props = {
  visible: boolean;
  onClose: () => void;
  onSelectSession: (sessionId: number) => void;
  currentSessionId?: number;
};

export function SessionDrawer({ visible, onClose, onSelectSession, currentSessionId }: Props) {
  const slideAnim = useRef(new Animated.Value(1)).current; // 1 = hidden (right), 0 = visible
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);

  // Animate in/out
  useEffect(() => {
    Animated.spring(slideAnim, {
      toValue: visible ? 0 : 1,
      useNativeDriver: true,
      tension: 65,
      friction: 11,
    }).start();

    if (visible) fetchSessions();
  }, [visible]);

  const fetchSessions = async () => {
    setLoading(true);
    try {
      const data = await api.getSessions();
      setSessions(data.sessions || []);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' });
  };

  const translateX = slideAnim.interpolate({
    inputRange: [0, 1],
    outputRange: ['0%', '100%'],
  });

  if (!visible && slideAnim._value === 1) return null;

  return (
    <View style={StyleSheet.absoluteFill} pointerEvents={visible ? 'auto' : 'none'}>
      {/* Backdrop */}
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Animated.View
          style={[
            styles.backdropInner,
            { opacity: slideAnim.interpolate({ inputRange: [0, 1], outputRange: [1, 0] }) },
          ]}
        />
      </Pressable>

      {/* Drawer */}
      <Animated.View style={[styles.drawer, { transform: [{ translateX }] }]}>
        {/* Header */}
        <View style={styles.drawerHeader}>
          <Text style={styles.drawerTitle}>Riwayat Chat</Text>
          <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
            <Ionicons name="close" size={20} color={Colors.textSecondary} />
          </TouchableOpacity>
        </View>

        {/* New Chat button */}
        <TouchableOpacity
          style={styles.newChatBtn}
          onPress={() => { onSelectSession(0); onClose(); }}
        >
          <Ionicons name="add" size={16} color="#000" />
          <Text style={styles.newChatText}>Chat Baru</Text>
        </TouchableOpacity>

        {/* Session List */}
        {loading ? (
          <ActivityIndicator color={Colors.accent} style={{ marginTop: 24 }} />
        ) : (
          <FlatList
            data={sessions}
            keyExtractor={item => item.session_id.toString()}
            contentContainerStyle={styles.list}
            ListEmptyComponent={
              <Text style={styles.emptyText}>Belum ada riwayat chat</Text>
            }
            renderItem={({ item }) => {
              const isActive = item.session_id === currentSessionId;
              return (
                <TouchableOpacity
                  style={[styles.sessionItem, isActive && styles.sessionItemActive]}
                  onPress={() => { onSelectSession(item.session_id); onClose(); }}
                >
                  <Ionicons
                    name="chatbubble-outline"
                    size={14}
                    color={isActive ? Colors.accent : Colors.textSecondary}
                    style={{ marginTop: 2 }}
                  />
                  <View style={styles.sessionInfo}>
                    <Text style={[styles.sessionName, isActive && styles.sessionNameActive]} numberOfLines={1}>
                      {item.session_name || `Session ${item.session_id}`}
                    </Text>
                    <Text style={styles.sessionDate}>{formatDate(item.updated_at)}</Text>
                  </View>
                  {isActive && (
                    <View style={styles.activeDot} />
                  )}
                </TouchableOpacity>
              );
            }}
          />
        )}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    ...StyleSheet.absoluteFillObject,
  },
  backdropInner: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(0,0,0,0.5)',
  },
  drawer: {
    position: 'absolute',
    right: 0,
    top: 0,
    bottom: 0,
    width: '45%',
    backgroundColor: Colors.surface,
    borderLeftWidth: 1,
    borderLeftColor: Colors.border,
    paddingTop: 60,
  },
  drawerHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingBottom: 16,
    borderBottomWidth: 1,
    borderBottomColor: Colors.border,
  },
  drawerTitle: {
    ...Typography.body,
    color: Colors.textPrimary,
    fontWeight: '600',
  },
  closeBtn: {
    padding: 4,
  },
  newChatBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    margin: 12,
    padding: 10,
    backgroundColor: Colors.accent,
    borderRadius: 8,
    justifyContent: 'center',
  },
  newChatText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#000',
  },
  list: {
    paddingHorizontal: 12,
    paddingTop: 8,
  },
  emptyText: {
    ...Typography.caption,
    color: Colors.textSecondary,
    textAlign: 'center',
    marginTop: 24,
  },
  sessionItem: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    padding: 10,
    borderRadius: 8,
    marginBottom: 4,
  },
  sessionItemActive: {
    backgroundColor: 'rgba(204, 255, 0, 0.08)',
    borderWidth: 1,
    borderColor: 'rgba(204, 255, 0, 0.2)',
  },
  sessionInfo: {
    flex: 1,
  },
  sessionName: {
    fontSize: 12,
    color: Colors.textPrimary,
    fontWeight: '500',
  },
  sessionNameActive: {
    color: Colors.accent,
  },
  sessionDate: {
    ...Typography.caption,
    color: Colors.textSecondary,
    marginTop: 2,
  },
  activeDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: Colors.accent,
    marginTop: 4,
  },
});