import { View, Text, Image, TouchableOpacity, StyleSheet } from 'react-native';
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
};

export function ChatBubble({ role, content, timestamp, imageUri }: Props) {
  const isUser = role === 'user';
  const [liked, setLiked] = useState<'like' | 'dislike' | null>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await Clipboard.setStringAsync(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (isUser) {
    return (
      <View style={styles.wrapperUser}>
        <View style={styles.bubbleUser}>
          {imageUri && (
            <Image source={{ uri: imageUri }} style={styles.image} resizeMode="cover" />
          )}
          <Text style={styles.textUser}>{content}</Text>
        </View>
        {timestamp && (
          <Text style={styles.timestampUser}>{timestamp}</Text>
        )}
      </View>
    );
  }

  return (
    <View style={styles.wrapperAI}>
      {/* Avatar */}
      <View style={styles.avatarContainer}>
        <Image source={KlaudiaAvatar} style={styles.avatar} resizeMode="cover" />
      </View>

      {/* Bubble + Actions */}
      <View style={styles.aiContent}>
        <View style={styles.bubbleAI}>
          {imageUri && (
            <Image source={{ uri: imageUri }} style={styles.image} resizeMode="cover" />
          )}
          <Markdown style={markdownStyles}>{content}</Markdown>
        </View>

        {/* Action buttons */}
        <View style={styles.actions}>
          <TouchableOpacity onPress={handleCopy} style={styles.actionBtn}>
            <Ionicons
              name={copied ? 'checkmark-outline' : 'copy-outline'}
              size={14}
              color={copied ? Colors.accent : Colors.textSecondary}
            />
          </TouchableOpacity>

          <TouchableOpacity
            onPress={() => setLiked(prev => prev === 'like' ? null : 'like')}
            style={styles.actionBtn}
          >
            <Ionicons
              name={liked === 'like' ? 'thumbs-up' : 'thumbs-up-outline'}
              size={14}
              color={liked === 'like' ? Colors.accent : Colors.textSecondary}
            />
          </TouchableOpacity>

          <TouchableOpacity
            onPress={() => setLiked(prev => prev === 'dislike' ? null : 'dislike')}
            style={styles.actionBtn}
          >
            <Ionicons
              name={liked === 'dislike' ? 'thumbs-down' : 'thumbs-down-outline'}
              size={14}
              color={liked === 'dislike' ? '#ef4444' : Colors.textSecondary}
            />
          </TouchableOpacity>

          {timestamp && (
            <Text style={styles.timestamp}>{timestamp}</Text>
          )}
        </View>
      </View>
    </View>
  );
}

// Sesuaikan warna dengan theme app kamu
const markdownStyles = StyleSheet.create({
  body: {
    color: Colors.textPrimary,
    fontSize: 14,
    lineHeight: 20,
    margin: 0,
  },
  // Bold (**text**)
  strong: {
    fontWeight: '700',
    color: Colors.textPrimary,
  },
  // Italic (*text*)
  em: {
    fontStyle: 'italic',
    color: Colors.textPrimary,
  },
  // Inline code (`code`)
  code_inline: {
    backgroundColor: Colors.border,
    color: Colors.accent,
    fontFamily: 'Courier',
    fontSize: 13,
    paddingHorizontal: 4,
    paddingVertical: 1,
    borderRadius: 4,
  },
  // Code block (```code```)
  fence: {
    backgroundColor: Colors.border,
    borderRadius: 8,
    padding: 10,
    marginVertical: 6,
  },
  code_block: {
    color: Colors.textPrimary,
    fontFamily: 'Courier',
    fontSize: 13,
    lineHeight: 18,
  },
  // Heading
  heading1: {
    fontSize: 18,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: 6,
    marginTop: 4,
  },
  heading2: {
    fontSize: 16,
    fontWeight: '700',
    color: Colors.textPrimary,
    marginBottom: 4,
    marginTop: 4,
  },
  heading3: {
    fontSize: 15,
    fontWeight: '600',
    color: Colors.textPrimary,
    marginBottom: 4,
    marginTop: 4,
  },
  // List
  bullet_list: {
    marginVertical: 4,
  },
  ordered_list: {
    marginVertical: 4,
  },
  list_item: {
    flexDirection: 'row',
    marginVertical: 1,
  },
  bullet_list_icon: {
    color: Colors.textSecondary,
    marginRight: 6,
    lineHeight: 20,
  },
  ordered_list_icon: {
    color: Colors.textSecondary,
    marginRight: 6,
    lineHeight: 20,
  },
  // Blockquote
  blockquote: {
    borderLeftWidth: 3,
    borderLeftColor: Colors.accent,
    paddingLeft: 10,
    marginVertical: 4,
    opacity: 0.85,
  },
  // Horizontal rule
  hr: {
    backgroundColor: Colors.border,
    height: 1,
    marginVertical: 8,
  },
  // Paragraph — hapus margin default agar tidak ada spasi berlebih
  paragraph: {
    marginTop: 0,
    marginBottom: 4,
  },
  // Link
  link: {
    color: Colors.accent,
    textDecorationLine: 'underline',
  },
});

const styles = StyleSheet.create({
  // User bubble
  wrapperUser: {
    alignSelf: 'flex-end',
    marginVertical: 4,
    marginHorizontal: 20,
    maxWidth: '80%',
  },
  bubbleUser: {
    backgroundColor: Colors.accent,
    padding: 12,
    borderRadius: Radius.bubble,
    borderBottomRightRadius: 4,
  },
  textUser: {
    ...Typography.body,
    color: '#000000',
  },
  timestampUser: {
    ...Typography.caption,
    color: Colors.textSecondary,
    textAlign: 'right',
    marginTop: 4,
  },

  // AI bubble
  wrapperAI: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    marginVertical: 4,
    marginHorizontal: 12,
    maxWidth: '85%',
    gap: 8,
  },
  avatarContainer: {
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: 'rgba(221, 221, 221, 0.85)',
    overflow: 'hidden',
    flexShrink: 0,
    marginBottom: 20,
  },
  avatar: {
    width: '100%',
    height: '100%',
  },
  aiContent: {
    flex: 1,
  },
  bubbleAI: {
    backgroundColor: Colors.surface,
    padding: 12,
    borderRadius: Radius.bubble,
    borderBottomLeftRadius: 4,
    borderWidth: 1,
    borderColor: Colors.border,
  },

  // Shared
  image: {
    width: 200,
    height: 150,
    borderRadius: 8,
    marginBottom: 8,
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 6,
    gap: 4,
  },
  actionBtn: {
    padding: 4,
    borderRadius: 6,
  },
  timestamp: {
    ...Typography.caption,
    color: Colors.textSecondary,
    marginLeft: 4,
  },
});