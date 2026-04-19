import { View, Text, Image, TouchableOpacity, StyleSheet } from 'react-native';
import { useState } from 'react';
import { Ionicons } from '@expo/vector-icons';
import * as Clipboard from 'expo-clipboard';
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
          <Text style={styles.textAI}>{content}</Text>
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
    marginBottom: 20, // align with action buttons row
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
  textAI: {
    ...Typography.body,
    color: Colors.textPrimary,
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