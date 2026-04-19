import { View, TextInput, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons, MaterialIcons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import { Colors, Radius } from '../../constants/theme';

type Attachment = {
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
  disabled?: boolean;
};

export function ChatInput({ value, onChangeText, onSend, onAttachment, disabled }: Props) {

  const openCamera = async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') return;
    const result = await ImagePicker.launchCameraAsync({
      base64: true,
      quality: 0.8,
    });
    if (!result.canceled && result.assets[0]) {
      const asset = result.assets[0];
      onAttachment({
        uri: asset.uri,
        name: 'camera_photo.jpg',
        type: 'image',
        base64: asset.base64 ?? undefined,
      });
    }
  };

  const openGallery = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') return;
    const result = await ImagePicker.launchImageLibraryAsync({
      base64: true,
      quality: 0.8,
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
    });
    if (!result.canceled && result.assets[0]) {
      const asset = result.assets[0];
      onAttachment({
        uri: asset.uri,
        name: asset.fileName ?? 'photo.jpg',
        type: 'image',
        base64: asset.base64 ?? undefined,
      });
    }
  };

  const openFilePicker = async () => {
    const result = await DocumentPicker.getDocumentAsync({
      type: 'application/pdf',
      copyToCacheDirectory: true,
    });
    if (!result.canceled && result.assets[0]) {
      const asset = result.assets[0];
      onAttachment({
        uri: asset.uri,
        name: asset.name,
        type: 'pdf',
      });
    }
  };

  return (
    <View style={styles.container}>
      {/* Utility buttons */}
      <View style={styles.utilities}>
        <TouchableOpacity onPress={openCamera} style={styles.utilBtn}>
          <Ionicons name="camera-outline" size={20} color={Colors.textSecondary} />
        </TouchableOpacity>
        <TouchableOpacity onPress={openGallery} style={styles.utilBtn}>
          <Ionicons name="image-outline" size={20} color={Colors.textSecondary} />
        </TouchableOpacity>
        <TouchableOpacity onPress={openFilePicker} style={styles.utilBtn}>
          <Ionicons name="document-outline" size={20} color={Colors.textSecondary} />
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
        style={[styles.sendBtn, disabled && styles.sendBtnDisabled]}
        onPress={onSend}
        disabled={disabled}
      >
        <Ionicons name="arrow-up" size={20} color="#000000" />
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 12,
    backgroundColor: Colors.surface,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
    gap: 8,
  },
  utilities: {
    flexDirection: 'row',
    gap: 4,
  },
  utilBtn: {
    padding: 6,
  },
  input: {
    flex: 1,
    backgroundColor: '#1C1C1E',
    borderRadius: Radius.pill,
    paddingHorizontal: 16,
    paddingVertical: 10,
    color: Colors.textPrimary,
    fontSize: 14,
    maxHeight: 100,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  sendBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.pill,
    width: 36,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  sendBtnDisabled: {
    opacity: 0.5,
  },
});