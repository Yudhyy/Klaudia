import { Ionicons } from '@expo/vector-icons';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { useEffect, useRef } from 'react';
import {
  Alert,
  Animated,
  Image,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';

import { Colors } from '../../constants/theme';

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_PDF_BYTES = 50 * 1024 * 1024;

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

async function requestPermission(
  requester: () => Promise<ImagePicker.PermissionResponse>,
  label: string,
): Promise<boolean> {
  const { status } = await requester();
  if (status === 'granted') {
    return true;
  }
  Alert.alert(
    'Permission Required',
    `Klaudia needs ${label} access to upload receipts. Enable it in Settings.`,
    [{ text: 'OK' }],
  );
  return false;
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment;
  onRemove: () => void;
}): React.JSX.Element {
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const scaleAnim = useRef(new Animated.Value(0.88)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeAnim, { toValue: 1, duration: 180, useNativeDriver: true }),
      Animated.spring(scaleAnim, {
        toValue: 1, tension: 200, friction: 16, useNativeDriver: true,
      }),
    ]).start();
  }, [fadeAnim, scaleAnim]);

  return (
    <Animated.View
      style={[
        styles.chipWrapper,
        { opacity: fadeAnim, transform: [{ scale: scaleAnim }] },
      ]}
    >
      {attachment.type === 'image' ? (
        <View style={styles.imageChip}>
          <Image source={{ uri: attachment.uri }} style={styles.chipThumb} />
          <View style={styles.chipLabelRow}>
            <Ionicons name="image-outline" size={11} color={Colors.textSecondary} />
            <Text style={styles.chipLabel} numberOfLines={1}>
              {attachment.name}
            </Text>
          </View>
        </View>
      ) : (
        <View style={styles.pdfChip}>
          <View style={styles.pdfIcon}>
            <Ionicons name="document-text-outline" size={18} color={Colors.accent} />
          </View>
          <Text style={styles.pdfLabel} numberOfLines={1}>
            {attachment.name}
          </Text>
        </View>
      )}
      <Pressable onPress={onRemove} style={styles.chipRemove} hitSlop={8}>
        <View style={styles.chipRemoveBg}>
          <Ionicons name="close" size={10} color="#000" />
        </View>
      </Pressable>
    </Animated.View>
  );
}

export function ChatInput({
  value,
  onChangeText,
  onSend,
  onAttachment,
  onRemoveAttachment,
  attachment,
  disabled,
}: Props): React.JSX.Element {
  const selectImage = async (source: 'camera' | 'library'): Promise<void> => {
    try {
      const permissionRequester =
        source === 'camera'
          ? ImagePicker.requestCameraPermissionsAsync
          : ImagePicker.requestMediaLibraryPermissionsAsync;
      const hasPermission = await requestPermission(
        permissionRequester,
        source === 'camera' ? 'camera' : 'photo library',
      );
      if (!hasPermission) return;

      const result =
        source === 'camera'
          ? await ImagePicker.launchCameraAsync({ quality: 0.8 })
          : await ImagePicker.launchImageLibraryAsync({
              quality: 0.8,
              mediaTypes: ['images'],
            });
      const asset = result.canceled ? undefined : result.assets[0];
      if (asset === undefined) return;

      const base64 = await readFileAsBase64(
        asset.uri,
        asset.fileSize,
        MAX_IMAGE_BYTES,
        'Gambar maksimal 10 MB.',
      );
      if (base64 === null) return;
      onAttachment({
        uri: asset.uri,
        name: asset.fileName ?? 'photo.jpg',
        type: 'image',
        base64,
      });
    } catch {
      Alert.alert('Error', 'Gagal membuka atau membaca gambar. Coba lagi.');
    }
  };

  const openFilePicker = async (): Promise<void> => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: 'application/pdf',
        copyToCacheDirectory: true,
      });
      const asset = result.canceled ? undefined : result.assets[0];
      if (asset === undefined) return;

      const base64 = await readFileAsBase64(
        asset.uri,
        asset.size,
        MAX_PDF_BYTES,
        'PDF maksimal 50 MB.',
      );
      if (base64 === null) return;
      onAttachment({ uri: asset.uri, name: asset.name, type: 'pdf', base64 });
    } catch {
      Alert.alert('Error', 'Gagal membuka atau membaca PDF. Coba lagi.');
    }
  };

  const canSend = !disabled && (value.trim().length > 0 || !!attachment);

  return (
    <View style={styles.container}>
      <View style={styles.composerBox}>
        {attachment && (
          <View style={styles.chipArea}>
            <AttachmentChip attachment={attachment} onRemove={onRemoveAttachment} />
          </View>
        )}

        <View style={styles.inputRow}>
          <View style={styles.utilities}>
            <TouchableOpacity
              onPress={() => void selectImage('camera')}
              style={styles.utilBtn}
              disabled={disabled}
            >
              <Ionicons
                name="camera-outline"
                size={19}
                color={disabled ? Colors.border : Colors.textSecondary}
              />
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => void selectImage('library')}
              style={styles.utilBtn}
              disabled={disabled}
            >
              <Ionicons
                name="image-outline"
                size={19}
                color={disabled ? Colors.border : Colors.textSecondary}
              />
            </TouchableOpacity>
            <TouchableOpacity
              onPress={openFilePicker}
              style={styles.utilBtn}
              disabled={disabled}
            >
              <Ionicons
                name="document-outline"
                size={19}
                color={disabled ? Colors.border : Colors.textSecondary}
              />
            </TouchableOpacity>
          </View>

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

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        reject(new Error('File reader returned an invalid value.'));
        return;
      }
      const separatorIndex = reader.result.indexOf(',');
      if (separatorIndex === -1 || separatorIndex === reader.result.length - 1) {
        reject(new Error('File reader returned an invalid data URL.'));
        return;
      }
      resolve(reader.result.slice(separatorIndex + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read file.'));
    reader.readAsDataURL(blob);
  });
}

async function readFileAsBase64(
  uri: string,
  size: number | undefined,
  maximumBytes: number,
  sizeError: string,
): Promise<string | null> {
  if (size !== undefined && size > maximumBytes) {
    Alert.alert('File terlalu besar', sizeError);
    return null;
  }
  const response = await fetch(uri);
  if (!response.ok && response.status !== 0) {
    throw new Error(`File read failed with status ${response.status}.`);
  }
  const blob = await response.blob();
  if (blob.size > maximumBytes) {
    Alert.alert('File terlalu besar', sizeError);
    return null;
  }
  return blobToBase64(blob);
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
  composerBox: {
    backgroundColor: '#1C1C1E',
    borderRadius: 22,
    borderWidth: 1,
    borderColor: Colors.border,
    overflow: 'hidden',
  },
  chipArea: {
    paddingTop: 10,
    paddingHorizontal: 12,
    paddingBottom: 4,
  },
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
