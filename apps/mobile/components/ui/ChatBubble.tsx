// components/ui/ChatBubble.tsx
import { View, Text, Image, TouchableOpacity, StyleSheet, Pressable } from 'react-native';
import { useState } from 'react';
import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
import Markdown from 'react-native-markdown-display';
import { Colors, Typography, Radius } from '../../constants/theme';

const KlaudiaAvatar = require('../../assets/klaudia.jpg');

type Props = {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  imageUri?: string;
  /** True while tokens are still arriving */
  streaming?: boolean;
};

// ─── Typing indicator ─────────────────────────────────────────────────────────

function TypingDots() {
  return (
    <View style={styles.dotsRow}>
      {[0.35, 0.65, 1].map((opacity, i) => (
        <View key={i} style={[styles.dot, { opacity }]} />
      ))}
    </View>
  );
}

// ─── Compact image thumbnail shown in user bubble after send ──────────────────

function SentImageThumb({ uri }: { uri: string }) {
  return (
    <View style={styles.sentImageWrapper}>
      <Image source={{ uri }} style={styles.sentImage} resizeMode="cover" />
    </View>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export function ChatBubble({ role, content, timestamp, imageUri, streaming }: Props) {
  const isUser = role === 'user';
  const [liked, setLiked] = useState<'like' | 'dislike' | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await Clipboard.setStringAsync(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // ── User bubble ─────────────────────────────────────────────────────────────
  if (isUser) {
    return (
      <View style={styles.wrapperUser}>
        {/* Image sent as compact thumb above text — only if actually has content too */}
        {imageUri && (
          <SentImageThumb uri={imageUri} />
        )}
        {/* Only show text bubble if there's actual text */}
        {content.trim().length > 0 && (
          <View style={styles.bubbleUser}>
            <Text style={styles.textUser}>{content}</Text>
          </View>
        )}
        {timestamp && <Text style={styles.timestampUser}>{timestamp}</Text>}
      </View>
    );
  }

  // ── AI bubble ─────────────────────────────────────────────────────────────
  const showDots = streaming && !content;
  const displayContent = streaming && content ? content + ' ▌' : content;

  return (
    <View style={styles.wrapperAI}>
      {/* Avatar */}
      <View style={styles.avatarContainer}>
        <Image source={KlaudiaAvatar} style={styles.avatar} resizeMode="cover" />
      </View>

      <View style={styles.aiContent}>
        <View style={styles.bubbleAI}>
          {showDots ? (
            <TypingDots />
          ) : (
            <Markdown style={markdownStyles}>{displayContent}</Markdown>
          )}
        </View>

        {/* Action row — hidden while streaming */}
        {!streaming && (
          <View style={styles.actions}>
            <Pressable onPress={handleCopy} style={styles.actionBtn}>
              <Ionicons
                name={copied ? 'checkmark' : 'copy-outline'}
                size={13}
                color={copied ? Colors.accent : Colors.textSecondary}
              />
            </Pressable>

            <Pressable
              onPress={() => setLiked(prev => prev === 'like' ? null : 'like')}
              style={styles.actionBtn}
            >
              <Ionicons
                name={liked === 'like' ? 'thumbs-up' : 'thumbs-up-outline'}
                size={13}
                color={liked === 'like' ? Colors.accent : Colors.textSecondary}
              />
            </Pressable>

            <Pressable
              onPress={() => setLiked(prev => prev === 'dislike' ? null : 'dislike')}
              style={styles.actionBtn}
            >
              <Ionicons
                name={liked === 'dislike' ? 'thumbs-down' : 'thumbs-down-outline'}
                size={13}
                color={liked === 'dislike' ? '#ef4444' : Colors.textSecondary}
              />
            </Pressable>

            {timestamp && (
              <Text style={styles.timestamp}>{timestamp}</Text>
            )}
          </View>
        )}
      </View>
    </View>
  );
}

// ─── Markdown styles ─────────────────────────────────────────────────────────

const markdownStyles = StyleSheet.create({
  body: { color: Colors.textPrimary, fontSize: 14, lineHeight: 21, margin: 0 },
  strong: { fontWeight: '700', color: Colors.textPrimary },
  em: { fontStyle: 'italic', color: Colors.textPrimary },
  code_inline: {
    backgroundColor: 'rgba(255,255,255,0.08)',
    color: Colors.accent,
    fontFamily: 'Courier',
    fontSize: 13,
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 4,
  },
  fence: {
    backgroundColor: '#111113',
    borderRadius: 8,
    padding: 12,
    marginVertical: 6,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  code_block: { color: Colors.textPrimary, fontFamily: 'Courier', fontSize: 12, lineHeight: 18 },
  heading1: { fontSize: 17, fontWeight: '700', color: Colors.textPrimary, marginBottom: 6, marginTop: 6 },
  heading2: { fontSize: 15, fontWeight: '700', color: Colors.textPrimary, marginBottom: 4, marginTop: 4 },
  heading3: { fontSize: 14, fontWeight: '600', color: Colors.textPrimary, marginBottom: 4, marginTop: 4 },
  bullet_list: { marginVertical: 3 },
  ordered_list: { marginVertical: 3 },
  list_item: { flexDirection: 'row', marginVertical: 2 },
  bullet_list_icon: { color: Colors.accent, marginRight: 8, lineHeight: 21, fontSize: 12 },
  ordered_list_icon: { color: Colors.textSecondary, marginRight: 6, lineHeight: 21 },
  blockquote: {
    borderLeftWidth: 2,
    borderLeftColor: Colors.accent,
    paddingLeft: 10,
    marginVertical: 4,
    opacity: 0.8,
  },
  hr: { backgroundColor: Colors.border, height: 1, marginVertical: 10 },
  paragraph: { marginTop: 0, marginBottom: 4 },
  link: { color: Colors.accent, textDecorationLine: 'underline' },
  table: { borderWidth: 1, borderColor: Colors.border, borderRadius: 6, marginVertical: 6 },
  th: { backgroundColor: 'rgba(204,255,0,0.06)', padding: 8 },
  td: { padding: 8, borderTopWidth: 1, borderTopColor: Colors.border },
  tr: { flexDirection: 'row' },
});

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  // ── User bubble
  wrapperUser: {
    alignSelf: 'flex-end',
    marginVertical: 3,
    marginHorizontal: 16,
    maxWidth: '78%',
    alignItems: 'flex-end',
    gap: 4,
  },
  sentImageWrapper: {
    borderRadius: 14,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  sentImage: {
    width: 200,
    height: 150,
    borderRadius: 14,
  },
  bubbleUser: {
    backgroundColor: Colors.accent,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 18,
    borderBottomRightRadius: 4,
  },
  textUser: {
    fontSize: 14,
    lineHeight: 20,
    color: '#000',
    fontWeight: '400',
  },
  timestampUser: {
    fontSize: 10,
    color: Colors.textSecondary,
    marginTop: 2,
  },

  // ── AI bubble
  wrapperAI: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginVertical: 3,
    marginHorizontal: 12,
    maxWidth: '88%',
    gap: 8,
  },
  avatarContainer: {
    width: 28,
    height: 28,
    borderRadius: 14,
    overflow: 'hidden',
    flexShrink: 0,
    marginBottom: 22,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
  },
  avatar: { width: '100%', height: '100%' },

  aiContent: { flex: 1 },
  bubbleAI: {
    backgroundColor: Colors.surface,
    paddingHorizontal: 14,
    paddingVertical: 11,
    borderRadius: 18,
    borderBottomLeftRadius: 4,
    borderWidth: 1,
    borderColor: Colors.border,
  },

  // ── Typing dots
  dotsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingVertical: 5,
    paddingHorizontal: 2,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: Colors.textSecondary,
  },

  // ── Actions
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 5,
    gap: 2,
    paddingLeft: 2,
  },
  actionBtn: {
    padding: 5,
    borderRadius: 6,
  },
  timestamp: {
    fontSize: 10,
    color: Colors.textSecondary,
    marginLeft: 6,
  },
});