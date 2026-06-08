// components/ui/ChatInput.tsx
import {
  Alert, View, TextInput, TouchableOpacity,
  StyleSheet, Image, Text, Animated, Pressable,
} from 'react-native';
import { useRef, useEffect } from 'react';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system';
import { Colors, Radius } from '../../constants/theme';

export type Attachment = {
  uri: string;
  name: string;
  type: 'image' | 'pdf';
  base64?: string;
};

type Props = {
  value: string;
  onChangeText: (text: string) => void;
  onSend: () => void;
  onAttachment: (attachment: Attachment) => void;
  onRemoveAttachment: () => void;
  attachment?: Attachment | null;
  disabled?: boolean;
};

// ─── Permission helper ────────────────────────────────────────────────────────

async function requestPermission(
  requester: () => Promise<ImagePicker.PermissionResponse>,
  label: string,
): Promise<boolean> {
  const { status } = await requester();
  if (status === 'granted') return true;
  Alert.alert(
    'Permission Required',
    `Klaudia needs ${label} access to upload receipts. Enable it in Settings.`,
    [{ text: 'OK' }],
  );
  return false;
}

// ─── Attachment chip inside composer ─────────────────────────────────────────

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}) {
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const scaleAnim = useRef(new Animated.Value(0.88)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeAnim, { toValue: 1, duration: 180, useNativeDriver: true }),
      Animated.spring(scaleAnim, {
        toValue: 1, tension: 200, friction: 16, useNativeDriver: true,
      }),
    ]).start();
  }, []);

  return (
    <Animated.View style={[
      styles.chipWrapper,
      { opacity: fadeAnim, transform: [{ scale: scaleAnim }] },
    ]}>
      {attachment.type === 'image' ? (
        <View style={styles.imageChip}>
          <Image source={{ uri: attachment.uri }} style={styles.chipThumb} />
          <View style={styles.chipLabelRow}>
            <Ionicons name="image-outline" size={11} color={Colors.textSecondary} />
            <Text style={styles.chipLabel} numberOfLines={1}>{attachment.name}</Text>
          </View>
        </View>
      ) : (
        <View style={styles.pdfChip}>
          <View style={styles.pdfIcon}>
            <Ionicons name="document-text-outline" size={18} color={Colors.accent} />
          </View>
          <Text style={styles.pdfLabel} numberOfLines={1}>{attachment.name}</Text>
        </View>
      )}
      <Pressable
        onPress={onRemove}
        style={styles.chipRemove}
        hitSlop={8}
      >
        <View style={styles.chipRemoveBg}>
          <Ionicons name="close" size={10} color="#000" />
        </View>
      </Pressable>
    </Animated.View>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export function ChatInput({
  value,
  onChangeText,
  onSend,
  onAttachment,
  onRemoveAttachment,
  attachment,
  disabled,
}: Props) {

  const openCamera = async () => {
    const ok = await requestPermission(ImagePicker.requestCameraPermissionsAsync, 'camera');
    if (!ok) return;
    const result = await ImagePicker.launchCameraAsync({ base64: true, quality: 0.8 });
    if (result.canceled || !result.assets[0]) return;
    const asset = result.assets[0];
    onAttachment({ uri: asset.uri, name: 'camera_photo.jpg', type: 'image', base64: asset.base64 ?? undefined });
  };

  const openGallery = async () => {
    const ok = await requestPermission(ImagePicker.requestMediaLibraryPermissionsAsync, 'photo library');
    if (!ok) return;
    const result = await ImagePicker.launchImageLibraryAsync({
      base64: true, quality: 0.8, mediaTypes: ['images'] as any,
    });
    if (result.canceled || !result.assets[0]) return;
    const asset = result.assets[0];
    onAttachment({ uri: asset.uri, name: asset.fileName ?? 'photo.jpg', type: 'image', base64: asset.base64 ?? undefined });
  };

  const openFilePicker = async () => {
    const result = await DocumentPicker.getDocumentAsync({ type: 'application/pdf', copyToCacheDirectory: true });
    if (result.canceled || !result.assets[0]) return;
    const asset = result.assets[0];
    let base64: string | undefined;
  try {
    // expo-file-system can't read drag-dropped files on iOS Simulator because
    // the URI lands outside the app sandbox even after copyToCacheDirectory.
    // fetch() goes through NSURLSession which handles cross-sandbox file:// URIs.
    const response = await fetch(asset.uri);
    const blob = await response.blob();
    base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        // strip the data URL prefix: "data:application/pdf;base64,"
        resolve(result.split(',')[1]);
      };
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  } catch {
    Alert.alert('Error', 'Failed to read PDF file. Please try again.');
    return;
  }

  onAttachment({ uri: asset.uri, name: asset.name, type: 'pdf', base64 });
};

  const canSend = !disabled && (value.trim().length > 0 || !!attachment);

  return (
    <View style={styles.container}>
      {/* ── Composer box — contains chip + text input together ── */}
      <View style={styles.composerBox}>
        {/* Attachment chip — lives inside the composer, above the text row */}
        {attachment && (
          <View style={styles.chipArea}>
            <AttachmentChip attachment={attachment} onRemove={onRemoveAttachment} />
          </View>
        )}

        {/* Text + utility row */}
        <View style={styles.inputRow}>
          {/* Media utility buttons — left side, inside composer */}
          <View style={styles.utilities}>
            <TouchableOpacity onPress={openCamera} style={styles.utilBtn} disabled={disabled}>
              <Ionicons name="camera-outline" size={19} color={disabled ? Colors.border : Colors.textSecondary} />
            </TouchableOpacity>
            <TouchableOpacity onPress={openGallery} style={styles.utilBtn} disabled={disabled}>
              <Ionicons name="image-outline" size={19} color={disabled ? Colors.border : Colors.textSecondary} />
            </TouchableOpacity>
            <TouchableOpacity onPress={openFilePicker} style={styles.utilBtn} disabled={disabled}>
              <Ionicons name="document-outline" size={19} color={disabled ? Colors.border : Colors.textSecondary} />
            </TouchableOpacity>
          </View>

          {/* Text input */}
          <TextInput
            autoCorrect={false}
            spellCheck={false}
            autoCapitalize="none"
            style={styles.input}
            placeholder="Message"
            placeholderTextColor={Colors.textSecondary}
            value={value}
            onChangeText={onChangeText}
            multiline
            maxLength={2000}
            editable={!disabled}
          />

          {/* Send button */}
          <TouchableOpacity
            style={[styles.sendBtn, !canSend && styles.sendBtnDisabled]}
            onPress={onSend}
            disabled={!canSend}
            activeOpacity={0.75}
          >
            <Ionicons name="arrow-up" size={18} color="#000" />
          </TouchableOpacity>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    paddingBottom: 4,
    backgroundColor: Colors.background,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: Colors.border,
  },

  // The rounded pill that wraps chip + input row
  composerBox: {
    backgroundColor: '#1C1C1E',
    borderRadius: 22,
    borderWidth: 1,
    borderColor: Colors.border,
    overflow: 'hidden',
  },

  // Chip area — shown only when attachment exists
  chipArea: {
    paddingTop: 10,
    paddingHorizontal: 12,
    paddingBottom: 4,
  },

  // Text + button row
  inputRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    paddingHorizontal: 4,
    paddingVertical: 6,
    gap: 2,
  },

  utilities: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingBottom: 2,
  },
  utilBtn: {
    width: 32,
    height: 32,
    alignItems: 'center',
    justifyContent: 'center',
  },

  input: {
    flex: 1,
    color: Colors.textPrimary,
    fontSize: 15,
    lineHeight: 20,
    maxHeight: 120,
    paddingVertical: 6,
    paddingHorizontal: 4,
  },

  sendBtn: {
    backgroundColor: Colors.accent,
    borderRadius: 18,
    width: 34,
    height: 34,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 2,
    marginRight: 4,
  },
  sendBtnDisabled: {
    opacity: 0.35,
  },

  // ── Image chip ────────────────────────────────────────────────────────────
  chipWrapper: {
    position: 'relative',
    alignSelf: 'flex-start',
  },
  imageChip: {
    backgroundColor: Colors.surface,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: Colors.border,
    overflow: 'hidden',
    width: 88,
  },
  chipThumb: {
    width: 88,
    height: 66,
  },
  chipLabelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    paddingHorizontal: 6,
    paddingVertical: 4,
  },
  chipLabel: {
    color: Colors.textSecondary,
    fontSize: 10,
    flex: 1,
  },
  pdfChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(204,255,0,0.07)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(204,255,0,0.2)',
    paddingHorizontal: 10,
    paddingVertical: 8,
    maxWidth: 200,
  },
  pdfIcon: {
    width: 28,
    height: 28,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(204,255,0,0.1)',
    borderRadius: 6,
  },
  pdfLabel: {
    color: Colors.textPrimary,
    fontSize: 12,
    fontWeight: '500',
    flex: 1,
  },

  // Remove button — top-right badge on chip
  chipRemove: {
    position: 'absolute',
    top: -5,
    right: -5,
  },
  chipRemoveBg: {
    width: 16,
    height: 16,
    borderRadius: 8,
    backgroundColor: Colors.textSecondary,
    alignItems: 'center',
    justifyContent: 'center',
  },
});