import { Ionicons } from '@expo/vector-icons';
import { useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Colors, Radius, Spacing, Typography } from '../../constants/theme';
import { useAuth } from '../../contexts/AuthContext';
import { useSpreadsheet } from '../../contexts/SpreadsheetContext';

export default function ProfileScreen(): React.JSX.Element {
  const insets = useSafeAreaInsets();
  const { session, logout } = useAuth();
  const { activeSpreadsheet, error: spreadsheetError, refresh } = useSpreadsheet();
  const [isSigningOut, setIsSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string>();

  const signOut = async (): Promise<void> => {
    setIsSigningOut(true);
    setSignOutError(undefined);
    try {
      await logout();
    } catch {
      setSignOutError('Gagal menghapus sesi dari perangkat. Coba lagi.');
      setIsSigningOut(false);
    }
  };

  const initial = session?.username.slice(0, 1).toUpperCase() ?? '?';

  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={[styles.content, { paddingTop: insets.top + 24 }]}
        showsVerticalScrollIndicator={false}
      >
        <Text style={styles.pageTitle}>Profil</Text>

        <View style={styles.identityCard}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{initial}</Text>
          </View>
          <Text style={styles.username}>{session?.username}</Text>
          <Text style={styles.userId}>User #{session?.user_id}</Text>
        </View>

        <View style={styles.ledgerCard}>
          <View style={styles.ledgerHeader}>
            <View style={styles.ledgerIcon}>
              <Ionicons name="server-outline" size={20} color={Colors.accent} />
            </View>
            <View style={styles.ledgerCopy}>
              <Text style={styles.cardLabel}>LEDGER AKTIF</Text>
              <Text style={styles.ledgerName} numberOfLines={1}>
                {activeSpreadsheet?.name ?? 'Belum tersedia'}
              </Text>
            </View>
            <View style={[styles.statusDot, activeSpreadsheet === null && styles.statusDotOff]} />
          </View>

          {spreadsheetError !== null && (
            <View style={styles.errorBlock}>
              <Text style={styles.errorText}>{spreadsheetError}</Text>
              <Pressable onPress={() => void refresh()}>
                <Text style={styles.retryText}>Coba lagi</Text>
              </Pressable>
            </View>
          )}
        </View>

        <View style={styles.securityCard}>
          <Ionicons name="lock-closed-outline" size={18} color={Colors.textSecondary} />
          <Text style={styles.securityText}>
            Sesi login tersimpan aman di perangkat. Password tidak disimpan.
          </Text>
        </View>

        <Pressable
          style={({ pressed }) => [
            styles.signOutButton,
            pressed && styles.pressed,
            isSigningOut && styles.disabled,
          ]}
          onPress={() => void signOut()}
          disabled={isSigningOut}
          accessibilityRole="button"
          accessibilityLabel="Keluar dari akun"
        >
          {isSigningOut ? (
            <ActivityIndicator color="#FCA5A5" />
          ) : (
            <>
              <Ionicons name="log-out-outline" size={19} color="#FCA5A5" />
              <Text style={styles.signOutText}>Keluar</Text>
            </>
          )}
        </Pressable>
        {signOutError !== undefined && (
          <Text style={styles.signOutError}>{signOutError}</Text>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: Colors.background,
  },
  content: {
    flexGrow: 1,
    gap: 14,
    paddingHorizontal: Spacing.screenPadding,
    paddingBottom: 120,
  },
  pageTitle: {
    marginBottom: 8,
    color: Colors.textPrimary,
    fontSize: 24,
    fontWeight: '700',
  },
  identityCard: {
    alignItems: 'center',
    gap: 5,
    padding: 24,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.card,
    backgroundColor: Colors.surface,
  },
  avatar: {
    width: 72,
    height: 72,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 8,
    borderRadius: 36,
    backgroundColor: Colors.accent,
  },
  avatarText: {
    color: '#000000',
    fontSize: 30,
    fontWeight: '800',
  },
  username: {
    color: Colors.textPrimary,
    fontSize: 20,
    fontWeight: '700',
  },
  userId: {
    color: Colors.textSecondary,
    fontSize: 12,
  },
  ledgerCard: {
    gap: 12,
    padding: Spacing.cardPadding,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.card,
    backgroundColor: Colors.surface,
  },
  ledgerHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  ledgerIcon: {
    width: 42,
    height: 42,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 12,
    backgroundColor: 'rgba(204,255,0,0.1)',
  },
  ledgerCopy: {
    flex: 1,
    gap: 3,
  },
  cardLabel: {
    color: Colors.textSecondary,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.3,
  },
  ledgerName: {
    color: Colors.textPrimary,
    fontSize: 15,
    fontWeight: '600',
  },
  statusDot: {
    width: 9,
    height: 9,
    borderRadius: 5,
    backgroundColor: Colors.success,
  },
  statusDotOff: {
    backgroundColor: Colors.textSecondary,
  },
  errorBlock: {
    gap: 6,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: Colors.border,
  },
  errorText: {
    ...Typography.caption,
    color: '#FCA5A5',
  },
  retryText: {
    color: Colors.accent,
    fontSize: 12,
    fontWeight: '600',
  },
  securityCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    padding: Spacing.cardPadding,
    borderRadius: Radius.card,
    backgroundColor: '#1C1C1E',
  },
  securityText: {
    flex: 1,
    color: Colors.textSecondary,
    fontSize: 12,
    lineHeight: 18,
  },
  signOutButton: {
    minHeight: 50,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    marginTop: 8,
    borderWidth: 1,
    borderColor: '#7F1D1D',
    borderRadius: Radius.card,
    backgroundColor: '#231313',
  },
  signOutText: {
    color: '#FCA5A5',
    fontSize: 14,
    fontWeight: '700',
  },
  pressed: {
    opacity: 0.7,
  },
  disabled: {
    opacity: 0.5,
  },
  signOutError: {
    color: '#FCA5A5',
    fontSize: 12,
    textAlign: 'center',
  },
});
