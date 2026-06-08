// components/ui/ChatBubble.tsx
import { View, Text, Image, StyleSheet, Pressable, ScrollView } from 'react-native';
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

// ─── Custom table renderer ────────────────────────────────────────────────────
//
// react-native-markdown-display renders tables into fixed-width columns that
// word-wrap text into vertical stacks — "Dat e", "Mer cha nt" etc.
//
// Solution: intercept the `table` AST node and render our own ScrollView-based
// table instead. The `rules` prop accepts a map of token → render function.
//
// Column width strategy:
//   • We scan all cells to find the max char length per column
//   • Min width per column = max(80, charCount × 8) px — keeps short headers readable
//   • Header cells use accent tint bg, bold text
//   • Data cells alternate subtle row tints for scannability (zebra striping)

function extractTextFromNode(node: any): string {
  if (!node) return '';
  if (typeof node === 'string') return node;
  if (node.type === 'text') return node.content ?? '';
  if (node.children) {
    return node.children.map(extractTextFromNode).join('');
  }
  return '';
}

function TableRenderer({ node }: { node: any }) {
  // Parse thead and tbody from AST
  const thead = node.children?.find((c: any) => c.type === 'thead');
  const tbody = node.children?.find((c: any) => c.type === 'tbody');

  const headerRow = thead?.children?.[0]?.children ?? [];
  const dataRows  = tbody?.children ?? [];

  // Measure column widths: scan all cells, take max char count per col
  const allRows = [
    headerRow,
    ...dataRows.map((r: any) => r.children ?? []),
  ];
  const colCount = headerRow.length;
  const colWidths: number[] = Array(colCount).fill(0);

  allRows.forEach((row: any[]) => {
    row.forEach((cell: any, ci: number) => {
      const text = extractTextFromNode(cell);
      const len = text.length;
      colWidths[ci] = Math.max(colWidths[ci], len);
    });
  });

  // Convert char counts to pixel widths
  // Heuristic: ~7.5px per char, minimum 72px, maximum 160px
  const pixelWidths = colWidths.map(c => Math.min(160, Math.max(72, Math.ceil(c * 7.5))));

  return (
    <View style={table.wrapper}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={true}
        indicatorStyle="white"
        bounces={false}
        contentContainerStyle={table.scrollContent}
      >
        <View>
          {/* Header row */}
          {headerRow.length > 0 && (
            <View style={table.headerRow}>
              {headerRow.map((cell: any, ci: number) => (
                <View
                  key={ci}
                  style={[
                    table.headerCell,
                    { width: pixelWidths[ci] },
                    ci < headerRow.length - 1 && table.cellBorderRight,
                  ]}
                >
                  <Text style={table.headerText} numberOfLines={2}>
                    {extractTextFromNode(cell)}
                  </Text>
                </View>
              ))}
            </View>
          )}

          {/* Data rows */}
          {dataRows.map((row: any, ri: number) => {
            const cells = row.children ?? [];
            const isEven = ri % 2 === 0;
            return (
              <View
                key={ri}
                style={[
                  table.dataRow,
                  isEven ? table.dataRowEven : table.dataRowOdd,
                  ri === dataRows.length - 1 && table.lastRow,
                ]}
              >
                {cells.map((cell: any, ci: number) => {
                  // Check if cell has bold/strong children
                  const rawText = extractTextFromNode(cell);
                  const hasBold = cell.children?.some(
                    (n: any) => n.type === 'strong' || n.children?.some((nn: any) => nn.type === 'strong')
                  );
                  return (
                    <View
                      key={ci}
                      style={[
                        table.dataCell,
                        { width: pixelWidths[ci] },
                        ci < cells.length - 1 && table.cellBorderRight,
                      ]}
                    >
                      <Text
                        style={[table.dataText, hasBold && table.dataBold]}
                        numberOfLines={3}
                      >
                        {rawText}
                      </Text>
                    </View>
                  );
                })}
              </View>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

const TABLE_BORDER    = 'rgba(255,255,255,0.10)';
const TABLE_HEADER_BG = 'rgba(204,255,0,0.08)';
const ROW_EVEN_BG     = 'rgba(255,255,255,0.02)';
const ROW_ODD_BG      = 'transparent';

const table = StyleSheet.create({
  wrapper: {
    marginVertical: 8,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: TABLE_BORDER,
    overflow: 'hidden',
    // Hint to user that content scrolls
    backgroundColor: '#1A1A1C',
  },
  scrollContent: {
    flexGrow: 0,
  },
  headerRow: {
    flexDirection: 'row',
    backgroundColor: TABLE_HEADER_BG,
    borderBottomWidth: 1,
    borderBottomColor: TABLE_BORDER,
  },
  headerCell: {
    paddingHorizontal: 10,
    paddingVertical: 8,
    justifyContent: 'center',
  },
  headerText: {
    color: Colors.accent,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.2,
  },
  dataRow: {
    flexDirection: 'row',
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: TABLE_BORDER,
  },
  dataRowEven: { backgroundColor: ROW_EVEN_BG },
  dataRowOdd:  { backgroundColor: ROW_ODD_BG },
  lastRow:     {},
  dataCell: {
    paddingHorizontal: 10,
    paddingVertical: 8,
    justifyContent: 'center',
  },
  dataText: {
    color: Colors.textPrimary,
    fontSize: 12,
    lineHeight: 17,
  },
  dataBold: {
    fontWeight: '700',
    color: Colors.textPrimary,
  },
  cellBorderRight: {
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: TABLE_BORDER,
  },
});

// ─── Markdown custom rules — inject TableRenderer for `table` token ──────────

const markdownRules = {
  table: (node: any, _children: any, _parent: any, _styles: any) => (
    <TableRenderer key={node.key} node={node} />
  ),
};

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
            : (
              <Markdown
                style={markdownStyles}
                rules={markdownRules}
              >
                {displayContent}
              </Markdown>
            )
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
// `table` token is now handled by markdownRules above — no table styles needed here.
// All other container nodes have explicit backgroundColor to defeat light-mode defaults.

const BLOCKQUOTE_BG = 'rgba(204,255,0,0.06)';
const CODE_BG       = '#111113';

const markdownStyles = StyleSheet.create({
  body:       { color: Colors.textPrimary, fontSize: 14, lineHeight: 21, margin: 0, backgroundColor: 'transparent' },
  strong:     { fontWeight: '700', color: Colors.textPrimary },
  em:         { fontStyle: 'italic', color: Colors.textPrimary },
  del:        { textDecorationLine: 'line-through', color: Colors.textSecondary },
  paragraph:  { marginTop: 0, marginBottom: 6, backgroundColor: 'transparent' },

  heading1: { fontSize: 17, fontWeight: '700', color: Colors.textPrimary, marginBottom: 6, marginTop: 8, backgroundColor: 'transparent' },
  heading2: { fontSize: 15, fontWeight: '700', color: Colors.textPrimary, marginBottom: 4, marginTop: 6, backgroundColor: 'transparent' },
  heading3: { fontSize: 14, fontWeight: '600', color: Colors.textPrimary, marginBottom: 4, marginTop: 4, backgroundColor: 'transparent' },

  blockquote: {
    backgroundColor: BLOCKQUOTE_BG,
    borderLeftWidth: 3,
    borderLeftColor: Colors.accent,
    paddingLeft: 12,
    paddingRight: 10,
    paddingVertical: 8,
    marginVertical: 6,
    borderRadius: 4,
  },
  blockquote_text: {
    color: Colors.textPrimary,
    fontSize: 13,
    lineHeight: 20,
    fontStyle: 'italic',
    backgroundColor: 'transparent',
  },

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
    backgroundColor: CODE_BG,
    borderRadius: 8,
    padding: 12,
    marginVertical: 6,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
  },
  code_block: { color: Colors.textPrimary, fontFamily: 'Courier', fontSize: 12, lineHeight: 18, backgroundColor: 'transparent' },

  bullet_list:       { marginVertical: 3, backgroundColor: 'transparent' },
  ordered_list:      { marginVertical: 3, backgroundColor: 'transparent' },
  list_item:         { flexDirection: 'row', marginVertical: 2, backgroundColor: 'transparent' },
  bullet_list_icon:  { color: Colors.accent, marginRight: 8, lineHeight: 21, fontSize: 12 },
  ordered_list_icon: { color: Colors.textSecondary, marginRight: 6, lineHeight: 21 },

  hr:        { backgroundColor: 'rgba(255,255,255,0.10)', height: 1, marginVertical: 10 },
  link:      { color: Colors.accent, textDecorationLine: 'underline' },
  blocklink: { color: Colors.accent, textDecorationLine: 'underline' },
  image:     { borderRadius: 8, marginVertical: 4 },
});

// ─── Component styles ─────────────────────────────────────────────────────────

const styles = StyleSheet.create({
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
  sentImage: { width: 200, height: 150 },
  bubbleUser: {
    backgroundColor: Colors.accent,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 18,
    borderBottomRightRadius: 4,
  },
  textUser: { fontSize: 14, lineHeight: 20, color: '#000', fontWeight: '400' },
  timestampUser: { fontSize: 10, color: Colors.textSecondary, marginTop: 2 },

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

  dotsRow: { flexDirection: 'row', alignItems: 'center', gap: 5, paddingVertical: 5, paddingHorizontal: 2 },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: Colors.textSecondary },

  actions: { flexDirection: 'row', alignItems: 'center', marginTop: 4, gap: 2, paddingLeft: 2 },
  actionBtn: { width: 28, height: 28, alignItems: 'center', justifyContent: 'center', borderRadius: 6 },
  actionBtnPressed: { backgroundColor: 'rgba(255,255,255,0.06)' },
  timestamp: { fontSize: 10, color: Colors.textSecondary, marginLeft: 4 },
});