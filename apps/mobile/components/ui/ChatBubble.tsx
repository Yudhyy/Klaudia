// components/ui/ChatBubble.tsx
import { View, Text, Image, StyleSheet, Pressable } from 'react-native';
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
  streaming?: boolean;
};

// ─── Typing indicator ─────────────────────────────────────────────────────────

function TypingDots() {
  return (
    <View style={styles.dotsRow}>
      <View style={[styles.dot, { opacity: 0.35 }]} />
      <View style={[styles.dot, { opacity: 0.65 }]} />
      <View style={[styles.dot, { opacity: 1.0 }]} />
    </View>
  );
}

// ─── Compact sent image ───────────────────────────────────────────────────────

function SentImage({ uri }: { uri: string }) {
  return (
    <View style={styles.sentImageWrapper}>
      <Image source={{ uri }} style={styles.sentImage} resizeMode="cover" />
    </View>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ChatBubble({ role, content, timestamp, imageUri, streaming }: Props) {
  const isUser = role === 'user';
  const [liked, setLiked] = useState<'like' | 'dislike' | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await Clipboard.setStringAsync(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // ── User bubble ──────────────────────────────────────────────────────────────
  if (isUser) {
    return (
      <View style={styles.wrapperUser}>
        {imageUri && <SentImage uri={imageUri} />}
        {content.trim().length > 0 && (
          <View style={styles.bubbleUser}>
            <Text style={styles.textUser}>{content}</Text>
          </View>
        )}
        {timestamp && <Text style={styles.timestampUser}>{timestamp}</Text>}
      </View>
    );
  }

  // ── AI bubble ────────────────────────────────────────────────────────────────
  const showDots = streaming && !content;
  const displayContent = streaming && content ? content + ' ▌' : content;

  return (
    <View style={styles.wrapperAI}>
      <View style={styles.avatarContainer}>
        <Image source={KlaudiaAvatar} style={styles.avatar} resizeMode="cover" />
      </View>

      <View style={styles.aiContent}>
        <View style={styles.bubbleAI}>
          {showDots
            ? <TypingDots />
            : <Markdown style={markdownStyles}>{displayContent}</Markdown>
          }
        </View>

        {!streaming && (
          <View style={styles.actions}>
            <Pressable
              onPress={handleCopy}
              style={({ pressed }) => [styles.actionBtn, pressed && styles.actionBtnPressed]}
              accessibilityLabel={copied ? 'Copied' : 'Copy message'}
            >
              <Ionicons
                name={copied ? 'checkmark' : 'copy-outline'}
                size={14}
                color={copied ? Colors.accent : Colors.textSecondary}
              />
            </Pressable>

            <Pressable
              onPress={() => setLiked(p => p === 'like' ? null : 'like')}
              style={({ pressed }) => [styles.actionBtn, pressed && styles.actionBtnPressed]}
              accessibilityLabel="Like"
            >
              <Ionicons
                name={liked === 'like' ? 'thumbs-up' : 'thumbs-up-outline'}
                size={14}
                color={liked === 'like' ? Colors.accent : Colors.textSecondary}
              />
            </Pressable>

            <Pressable
              onPress={() => setLiked(p => p === 'dislike' ? null : 'dislike')}
              style={({ pressed }) => [styles.actionBtn, pressed && styles.actionBtnPressed]}
              accessibilityLabel="Dislike"
            >
              <Ionicons
                name={liked === 'dislike' ? 'thumbs-down' : 'thumbs-down-outline'}
                size={14}
                color={liked === 'dislike' ? '#ef4444' : Colors.textSecondary}
              />
            </Pressable>

            {timestamp && <Text style={styles.timestamp}>{timestamp}</Text>}
          </View>
        )}
      </View>
    </View>
  );
}

// ─── Markdown styles ──────────────────────────────────────────────────────────
//
// react-native-markdown-display injects its own default stylesheet.
// For dark mode, three defaults are destructive and MUST be explicitly overridden:
//
//   blockquote        → default backgroundColor: '#fff6' (white tint) → renders as opaque block
//   blockquote_text   → default color: inherit from light theme        → unreadable
//   table, th, td     → default borders and backgrounds assume light bg
//   hr                → default backgroundColor: '#000' (visible on light, invisible on dark)
//   fence / code_block → default backgroundColor: '#f6f8fa' (GitHub light) → white box on dark
//
// Rule: ALWAYS set backgroundColor explicitly on every container node.
// Never rely on library defaults in a dark-only app.

const BLOCKQUOTE_BG   = 'rgba(204,255,0,0.06)';  // very faint accent tint — readable, non-intrusive
const BLOCKQUOTE_BAR  = Colors.accent;             // #CCFF00 left bar
const CODE_BG         = '#111113';                 // near-black, clearly distinct from bubble #252528
const TABLE_BORDER    = 'rgba(255,255,255,0.10)';

const markdownStyles = StyleSheet.create({
  // ── Root
  body: {
    color: Colors.textPrimary,
    fontSize: 14,
    lineHeight: 21,
    margin: 0,
    backgroundColor: 'transparent', // must be explicit
  },

  // ── Text
  strong:  { fontWeight: '700', color: Colors.textPrimary },
  em:      { fontStyle: 'italic', color: Colors.textPrimary },
  del:     { textDecorationLine: 'line-through', color: Colors.textSecondary },

  // ── Paragraph
  paragraph: { marginTop: 0, marginBottom: 6, backgroundColor: 'transparent' },

  // ── Headings
  heading1: { fontSize: 17, fontWeight: '700', color: Colors.textPrimary, marginBottom: 6, marginTop: 8, backgroundColor: 'transparent' },
  heading2: { fontSize: 15, fontWeight: '700', color: Colors.textPrimary, marginBottom: 4, marginTop: 6, backgroundColor: 'transparent' },
  heading3: { fontSize: 14, fontWeight: '600', color: Colors.textPrimary, marginBottom: 4, marginTop: 4, backgroundColor: 'transparent' },

  // ── Blockquote — THE BUG FIX
  // backgroundColor MUST be set here; the library default is a light tint
  blockquote: {
    backgroundColor: BLOCKQUOTE_BG,          // ← explicit dark-safe tint
    borderLeftWidth: 3,
    borderLeftColor: BLOCKQUOTE_BAR,
    paddingLeft: 12,
    paddingRight: 10,
    paddingVertical: 8,
    marginVertical: 6,
    borderRadius: 4,
  },
  // The inner text wrapper the library generates — must also be transparent
  // otherwise it renders over the blockquote background
  blockquote_text: {
    color: Colors.textPrimary,
    fontSize: 13,
    lineHeight: 20,
    fontStyle: 'italic',
    backgroundColor: 'transparent',          // ← critical: kills the white inner fill
  },

  // ── Code
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
    backgroundColor: CODE_BG,                // ← explicit, not library default (#f6f8fa)
    borderRadius: 8,
    padding: 12,
    marginVertical: 6,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  code_block: {
    color: Colors.textPrimary,
    fontFamily: 'Courier',
    fontSize: 12,
    lineHeight: 18,
    backgroundColor: 'transparent',          // ← sits inside fence which has its own bg
  },

  // ── Lists
  bullet_list:        { marginVertical: 3, backgroundColor: 'transparent' },
  ordered_list:       { marginVertical: 3, backgroundColor: 'transparent' },
  list_item:          { flexDirection: 'row', marginVertical: 2, backgroundColor: 'transparent' },
  bullet_list_icon:   { color: Colors.accent, marginRight: 8, lineHeight: 21, fontSize: 12 },
  ordered_list_icon:  { color: Colors.textSecondary, marginRight: 6, lineHeight: 21 },

  // ── Table
  table: {
    borderWidth: 1,
    borderColor: TABLE_BORDER,
    borderRadius: 6,
    marginVertical: 8,
    backgroundColor: 'transparent',
    overflow: 'hidden',
  },
  thead: { backgroundColor: 'rgba(255,255,255,0.04)' },
  tbody: { backgroundColor: 'transparent' },
  th: {
    padding: 8,
    backgroundColor: 'rgba(255,255,255,0.05)', // ← explicit, not library default
    borderRightWidth: 1,
    borderRightColor: TABLE_BORDER,
  },
  td: {
    padding: 8,
    borderTopWidth: 1,
    borderTopColor: TABLE_BORDER,
    borderRightWidth: 1,
    borderRightColor: TABLE_BORDER,
    backgroundColor: 'transparent',           // ← explicit
  },
  tr: {
    flexDirection: 'row',
    backgroundColor: 'transparent',
  },
  th_text: { color: Colors.textPrimary, fontWeight: '600', fontSize: 13 },
  td_text: { color: Colors.textPrimary, fontSize: 13 },

  // ── Divider
  hr: {
    backgroundColor: 'rgba(255,255,255,0.10)', // ← explicit; default is '#000' (invisible on dark)
    height: 1,
    marginVertical: 10,
  },

  // ── Link
  link: { color: Colors.accent, textDecorationLine: 'underline' },
  blocklink: { color: Colors.accent, textDecorationLine: 'underline' },

  // ── Image (rare in chat, but cover it)
  image: { borderRadius: 8, marginVertical: 4 },
});

// ─── Component styles ─────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  // ── User bubble
  wrapperUser: {
    alignSelf: 'flex-end',
    marginVertical: 2,
    marginHorizontal: 16,
    maxWidth: '78%',
    alignItems: 'flex-end',
    gap: 4,
  },
  sentImageWrapper: {
    borderRadius: 16,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  sentImage: {
    width: 200,
    height: 150,
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
    alignItems: 'flex-start',
    marginVertical: 2,
    marginLeft: 12,
    marginRight: 48,
    gap: 8,
  },
  avatarContainer: {
    width: 28,
    height: 28,
    borderRadius: 14,
    overflow: 'hidden',
    flexShrink: 0,
    marginTop: 2,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
  },
  avatar: { width: '100%', height: '100%' },
  aiContent: { flex: 1 },
  bubbleAI: {
    backgroundColor: '#252528',
    paddingHorizontal: 14,
    paddingVertical: 11,
    borderRadius: 18,
    borderTopLeftRadius: 4,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.07)',
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

  // ── Action bar
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 4,
    gap: 2,
    paddingLeft: 2,
  },
  actionBtn: {
    width: 28,
    height: 28,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 6,
  },
  actionBtnPressed: {
    backgroundColor: 'rgba(255,255,255,0.06)',
  },
  timestamp: {
    fontSize: 10,
    color: Colors.textSecondary,
    marginLeft: 4,
  },
});