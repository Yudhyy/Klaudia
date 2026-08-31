import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
import { useState } from 'react';
import {
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import Markdown, {
  type ASTNode,
  MarkdownIt,
  type RenderRules,
} from 'react-native-markdown-display';

import { Colors } from '../../constants/theme';

const KlaudiaAvatar = require('../../assets/klaudia.jpg');
const TABLE_MAX_WIDTH = 280;
const TABLE_MIN_COLUMN_WIDTH = 52;
const TABLE_MAX_COLUMN_WIDTH = 200;
const markdownParser = MarkdownIt({ typographer: true, linkify: false }).disable(['image']);

type ChatBubbleProps = {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  imageUri?: string;
  streaming?: boolean;
};

export function ChatBubble({
  role,
  content,
  timestamp,
  imageUri,
  streaming = false,
}: ChatBubbleProps): React.JSX.Element {
  const [copied, setCopied] = useState(false);

  if (role === 'user') {
    return (
      <View style={styles.userWrapper}>
        {imageUri !== undefined && <SentImage uri={imageUri} />}
        {content.length > 0 && (
          <View style={styles.userBubble}>
            <Text style={styles.userText}>{content}</Text>
          </View>
        )}
        {timestamp !== undefined && <Text style={styles.userTimestamp}>{timestamp}</Text>}
      </View>
    );
  }

  const copyMessage = async (): Promise<void> => {
    await Clipboard.setStringAsync(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  const showTyping = streaming && content.length === 0;
  const displayContent = streaming && content.length > 0 ? `${content} ▌` : content;

  return (
    <View style={styles.assistantWrapper}>
      <View style={styles.avatarContainer}>
        <Image source={KlaudiaAvatar} style={styles.avatar} resizeMode="cover" />
      </View>
      <View style={styles.assistantContent}>
        <View style={styles.assistantBubble}>
          {showTyping ? (
            <TypingDots />
          ) : (
            <Markdown
              style={markdownStyles}
              rules={markdownRules}
              markdownit={markdownParser}
              onLinkPress={allowExternalLink}
            >
              {displayContent}
            </Markdown>
          )}
        </View>
        {!streaming && content.length > 0 && (
          <View style={styles.actions}>
            <Pressable
              onPress={() => void copyMessage()}
              style={({ pressed }) => [styles.copyButton, pressed && styles.pressed]}
              accessibilityRole="button"
              accessibilityLabel={copied ? 'Pesan disalin' : 'Salin pesan'}
            >
              <Ionicons
                name={copied ? 'checkmark' : 'copy-outline'}
                size={14}
                color={copied ? Colors.accent : Colors.textSecondary}
              />
            </Pressable>
            {timestamp !== undefined && <Text style={styles.timestamp}>{timestamp}</Text>}
          </View>
        )}
      </View>
    </View>
  );
}

function TypingDots(): React.JSX.Element {
  return (
    <View style={styles.dotsRow}>
      <View style={[styles.dot, styles.dotFaint]} />
      <View style={[styles.dot, styles.dotMedium]} />
      <View style={styles.dot} />
    </View>
  );
}

function SentImage({ uri }: { uri: string }): React.JSX.Element {
  return (
    <View style={styles.sentImageWrapper}>
      <Image source={{ uri }} style={styles.sentImage} resizeMode="cover" />
    </View>
  );
}

function MarkdownTable({ node }: { node: ASTNode }): React.JSX.Element {
  const headerSection = node.children.find((child) => child.type === 'thead');
  const bodySection = node.children.find((child) => child.type === 'tbody');
  const headerCells = headerSection?.children[0]?.children ?? [];
  const bodyRows = (bodySection?.children ?? []).filter((row) =>
    row.children.some((cell) => nodeText(cell).trim().length > 0),
  );
  const rows = [headerCells, ...bodyRows.map((row) => row.children)];
  const columnWidths = calculateColumnWidths(rows);
  const tableWidth = columnWidths.reduce((total, width) => total + width, 0);
  const tableBody = (
    <View style={{ width: tableWidth }}>
      {headerCells.length > 0 && (
        <View style={tableStyles.headerRow}>
          {headerCells.map((cell, columnIndex) => (
            <View
              key={cell.key}
              style={[
                tableStyles.cell,
                tableStyles.headerCell,
                { width: columnWidths[columnIndex] },
                columnIndex < headerCells.length - 1 && tableStyles.rightBorder,
              ]}
            >
              <Text style={tableStyles.headerText} numberOfLines={2}>
                {nodeText(cell)}
              </Text>
            </View>
          ))}
        </View>
      )}
      {bodyRows.map((row, rowIndex) => (
        <View
          key={row.key}
          style={[tableStyles.dataRow, rowIndex % 2 === 0 && tableStyles.evenRow]}
        >
          {row.children.map((cell, columnIndex) => (
            <View
              key={cell.key}
              style={[
                tableStyles.cell,
                { width: columnWidths[columnIndex] },
                columnIndex < row.children.length - 1 && tableStyles.rightBorder,
              ]}
            >
              <Text
                style={[tableStyles.dataText, containsBold(cell) && tableStyles.boldText]}
                numberOfLines={3}
              >
                {nodeText(cell)}
              </Text>
            </View>
          ))}
        </View>
      ))}
    </View>
  );

  return (
    <View style={tableStyles.wrapper}>
      {tableWidth > TABLE_MAX_WIDTH ? (
        <ScrollView
          horizontal
          bounces={false}
          showsHorizontalScrollIndicator
          contentContainerStyle={tableStyles.scrollContent}
        >
          {tableBody}
        </ScrollView>
      ) : (
        tableBody
      )}
    </View>
  );
}

function calculateColumnWidths(rows: ASTNode[][]): number[] {
  const columnCount = Math.max(0, ...rows.map((row) => row.length));
  return Array.from({ length: columnCount }, (_, columnIndex) => {
    const longestCell = Math.max(
      0,
      ...rows.map((row) => nodeText(row[columnIndex]).length),
    );
    return Math.min(
      TABLE_MAX_COLUMN_WIDTH,
      Math.max(TABLE_MIN_COLUMN_WIDTH, longestCell * 7 + 20),
    );
  });
}

function nodeText(node: ASTNode | undefined): string {
  if (node === undefined) return '';
  if (node.type === 'text') return node.content;
  return node.children.map(nodeText).join('');
}

function containsBold(node: ASTNode): boolean {
  return node.type === 'strong' || node.children.some(containsBold);
}

function allowExternalLink(url: string): boolean {
  return url.startsWith('https://');
}

const markdownRules: RenderRules = {
  table: (node) => <MarkdownTable key={node.key} node={node} />,
};

const tableStyles = StyleSheet.create({
  wrapper: {
    alignSelf: 'flex-start',
    maxWidth: '100%',
    marginVertical: 8,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.10)',
    borderRadius: 10,
    backgroundColor: '#1A1A1C',
  },
  scrollContent: {
    flexGrow: 0,
  },
  headerRow: {
    flexDirection: 'row',
    backgroundColor: 'rgba(204,255,0,0.08)',
  },
  dataRow: {
    flexDirection: 'row',
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: 'rgba(255,255,255,0.10)',
  },
  evenRow: {
    backgroundColor: 'rgba(255,255,255,0.02)',
  },
  cell: {
    justifyContent: 'center',
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  headerCell: {
    minHeight: 36,
  },
  rightBorder: {
    borderRightWidth: StyleSheet.hairlineWidth,
    borderRightColor: 'rgba(255,255,255,0.10)',
  },
  headerText: {
    color: Colors.accent,
    fontSize: 12,
    fontWeight: '700',
  },
  dataText: {
    color: Colors.textPrimary,
    fontSize: 12,
    lineHeight: 17,
  },
  boldText: {
    fontWeight: '700',
  },
});

const markdownStyles = StyleSheet.create({
  body: {
    margin: 0,
    color: Colors.textPrimary,
    backgroundColor: 'transparent',
    fontSize: 14,
    lineHeight: 21,
  },
  paragraph: {
    marginTop: 0,
    marginBottom: 6,
  },
  strong: {
    color: Colors.textPrimary,
    fontWeight: '700',
  },
  em: {
    color: Colors.textPrimary,
    fontStyle: 'italic',
  },
  heading1: {
    marginTop: 8,
    marginBottom: 6,
    color: Colors.textPrimary,
    fontSize: 17,
    fontWeight: '700',
  },
  heading2: {
    marginTop: 6,
    marginBottom: 4,
    color: Colors.textPrimary,
    fontSize: 15,
    fontWeight: '700',
  },
  heading3: {
    marginTop: 4,
    marginBottom: 4,
    color: Colors.textPrimary,
    fontSize: 14,
    fontWeight: '600',
  },
  blockquote: {
    marginVertical: 6,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderLeftWidth: 3,
    borderLeftColor: Colors.accent,
    borderRadius: 4,
    backgroundColor: 'rgba(204,255,0,0.06)',
  },
  code_inline: {
    paddingHorizontal: 5,
    paddingVertical: 1,
    borderRadius: 4,
    color: Colors.accent,
    backgroundColor: 'rgba(255,255,255,0.08)',
    fontFamily: 'Courier',
    fontSize: 13,
  },
  fence: {
    marginVertical: 6,
    padding: 12,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 8,
    backgroundColor: '#111113',
  },
  code_block: {
    color: Colors.textPrimary,
    backgroundColor: 'transparent',
    fontFamily: 'Courier',
    fontSize: 12,
    lineHeight: 18,
  },
  bullet_list: {
    marginVertical: 3,
  },
  ordered_list: {
    marginVertical: 3,
  },
  list_item: {
    flexDirection: 'row',
    marginVertical: 2,
  },
  bullet_list_icon: {
    marginRight: 8,
    color: Colors.accent,
    fontSize: 12,
    lineHeight: 21,
  },
  ordered_list_icon: {
    marginRight: 6,
    color: Colors.textSecondary,
    lineHeight: 21,
  },
  hr: {
    height: 1,
    marginVertical: 10,
    backgroundColor: 'rgba(255,255,255,0.10)',
  },
  link: {
    color: Colors.accent,
    textDecorationLine: 'underline',
  },
});

const styles = StyleSheet.create({
  userWrapper: {
    alignSelf: 'flex-end',
    maxWidth: '82%',
    alignItems: 'flex-end',
    gap: 4,
    marginHorizontal: 16,
    marginVertical: 2,
  },
  sentImageWrapper: {
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.08)',
    borderRadius: 16,
  },
  sentImage: {
    width: 200,
    height: 150,
  },
  userBubble: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 18,
    borderBottomRightRadius: 4,
    backgroundColor: Colors.accent,
  },
  userText: {
    color: '#000000',
    fontSize: 14,
    lineHeight: 20,
  },
  userTimestamp: {
    marginTop: 2,
    color: Colors.textSecondary,
    fontSize: 10,
  },
  assistantWrapper: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    marginLeft: 12,
    marginRight: 40,
    marginVertical: 2,
  },
  avatarContainer: {
    width: 28,
    height: 28,
    flexShrink: 0,
    marginTop: 2,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: 14,
  },
  avatar: {
    width: '100%',
    height: '100%',
  },
  assistantContent: {
    flex: 1,
  },
  assistantBubble: {
    paddingHorizontal: 14,
    paddingVertical: 11,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.07)',
    borderRadius: 18,
    borderTopLeftRadius: 4,
    backgroundColor: '#252528',
  },
  dotsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 2,
    paddingVertical: 5,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: Colors.textSecondary,
  },
  dotFaint: {
    opacity: 0.35,
  },
  dotMedium: {
    opacity: 0.65,
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
    marginTop: 4,
    paddingLeft: 2,
  },
  copyButton: {
    width: 28,
    height: 28,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 6,
  },
  timestamp: {
    marginLeft: 4,
    color: Colors.textSecondary,
    fontSize: 10,
  },
  pressed: {
    backgroundColor: 'rgba(255,255,255,0.06)',
  },
});
