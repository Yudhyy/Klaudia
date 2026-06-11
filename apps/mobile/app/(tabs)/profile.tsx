import { Image } from 'expo-image';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

// ─── Design tokens ─────────────────────────────────────────────────────────────
const T = {
  bg: '#161618',
  surface: '#0C0C0E',
  accent: '#CCFF00',
  accentDim: 'rgba(204,255,0,0.15)',
  textPrimary: '#FFFFFF',
  textSecondary: '#A1A1AA',
  textMuted: '#71717A',
  success: '#16A34A',
  border: '#27272A',
  borderSubtle: '#1F1F22',
};

// ─── Types ─────────────────────────────────────────────────────────────────────
type IconSpec =
  | { lib: 'ionicons'; name: string }
  | { lib: 'mci'; name: string };

type MenuItemDef = {
  label: string;
  iconBg: string;
  iconColor: string;
  icon: IconSpec;
  isMCP?: boolean;
};

// ─── Menu data ─────────────────────────────────────────────────────────────────
const PRIMARY_MENU: MenuItemDef[] = [
  {
    label: 'Settings',
    iconBg: 'rgba(99,102,241,0.18)',
    iconColor: '#818CF8',
    icon: { lib: 'ionicons', name: 'settings-sharp' },
  },
  {
    label: 'MCP Connection',
    iconBg: 'rgba(22,163,74,0.15)',
    iconColor: '#16A34A',
    icon: { lib: 'mci', name: 'lan-connect' },
    isMCP: true,
  },
  {
    label: 'Activity History',
    iconBg: 'rgba(245,158,11,0.18)',
    iconColor: '#F59E0B',
    icon: { lib: 'ionicons', name: 'time-outline' },
  },
];

const SECONDARY_MENU: MenuItemDef[] = [
  {
    label: 'Contact Us',
    iconBg: 'rgba(59,130,246,0.18)',
    iconColor: '#60A5FA',
    icon: { lib: 'ionicons', name: 'mail-outline' },
  },
  {
    label: 'Privacy Policy',
    iconBg: 'rgba(161,161,170,0.12)',
    iconColor: '#A1A1AA',
    icon: { lib: 'ionicons', name: 'shield-checkmark-outline' },
  },
];

const STATS = [
  { val: '127', label: 'Receipts' },
  { val: '24',  label: 'This Month' },
  { val: '3',   label: 'Sheets' },
];

// ─── Icon renderer ─────────────────────────────────────────────────────────────
function MenuIcon({ icon, color }: { icon: IconSpec; color: string }) {
  if (icon.lib === 'ionicons') {
    return <Ionicons name={icon.name as any} size={20} color={color} />;
  }
  return <MaterialCommunityIcons name={icon.name as any} size={20} color={color} />;
}

// ─── MCP sub-row (Google Sheets · Connected) ───────────────────────────────────
function MCPConnectedRow() {
  return (
    <View style={styles.mcpRow}>
      <Image
        source={require('../../assets/gsheet_macos.png')}
        style={styles.mcpSheetIcon}
        contentFit="contain"
      />
      <Text style={styles.mcpLabel}>Google Sheets</Text>
      <View style={styles.mcpSep} />
      <Text style={styles.mcpConnected}>Connected</Text>
    </View>
  );
}

// ─── Single menu row ───────────────────────────────────────────────────────────
function MenuRow({ item, isLast }: { item: MenuItemDef; isLast: boolean }) {
  return (
    <Pressable
      style={({ pressed }) => [
        styles.menuItem,
        !isLast && styles.menuItemDivider,
        pressed && { opacity: 0.6 },
      ]}
      accessibilityRole="button"
      accessibilityLabel={item.label}
    >
      {/* Icon wrap */}
      <View style={[styles.menuIconWrap, { backgroundColor: item.iconBg }]}>
        <MenuIcon icon={item.icon} color={item.iconColor} />
      </View>

      {/* Label + optional MCP sub-row */}
      <View style={styles.menuLabelWrap}>
        <Text style={styles.menuLabel}>{item.label}</Text>
        {item.isMCP && <MCPConnectedRow />}
      </View>

      {/* Chevron */}
      <Ionicons name="chevron-forward" size={16} color={T.textMuted} />
    </Pressable>
  );
}

// ─── Menu group card ───────────────────────────────────────────────────────────
function MenuGroup({ items }: { items: MenuItemDef[] }) {
  return (
    <View style={styles.menuCard}>
      {items.map((item, i) => (
        <MenuRow key={item.label} item={item} isLast={i === items.length - 1} />
      ))}
    </View>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────
export default function ProfileScreen() {
  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
        contentInsetAdjustmentBehavior="automatic"
      >
        {/* ── Title ── */}
        <Text style={styles.pageTitle}>Profile</Text>

        {/* ── Avatar block ── */}
        <View style={styles.avatarBlock}>
          <View style={styles.avatarRingWrap}>
            <View style={styles.avatarRing}>
              <Image
                source={require('../../assets/avatar.jpg')}
                style={styles.avatarImg}
                contentFit="cover"
              />
            </View>
            <View style={styles.onlineDot} />
          </View>
          <Text style={styles.userName}>Yudhy McCodey</Text>
          <Text style={styles.userEmail}>yudhymccodey@gmail.com</Text>
          <View style={styles.proBadge}>
            <Text style={styles.proBadgeText}>Pro Plan</Text>
          </View>
        </View>

        {/* ── Stats ── */}
        <View style={styles.statsCard}>
          {STATS.map((s, i) => (
            <View
              key={s.label}
              style={[styles.statCell, i < STATS.length - 1 && styles.statCellBorder]}
            >
              <Text style={[styles.statVal, { fontVariant: ['tabular-nums'] }]}>
                {s.val}
              </Text>
              <Text style={styles.statLabel}>{s.label}</Text>
            </View>
          ))}
        </View>

        {/* ── Primary menu group ── */}
        <MenuGroup items={PRIMARY_MENU} />

        {/* ── Secondary menu group ── */}
        <MenuGroup items={SECONDARY_MENU} />

        {/* ── Sign Out ── */}
        <Pressable
          style={({ pressed }) => [styles.signOutBtn, pressed && { opacity: 0.6 }]}
          accessibilityRole="button"
          accessibilityLabel="Sign Out"
        >
          <Text style={styles.signOutText}>Sign Out</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: T.bg,
  },
  scroll: {
    paddingHorizontal: 20,
    paddingBottom: 100,
    gap: 12,
  },

  // Title
  pageTitle: {
    color: T.textPrimary,
    fontSize: 18,
    fontWeight: '700',
    textAlign: 'center',
    paddingTop: 60,
    paddingBottom: 4,
  },

  // Avatar
  avatarBlock: {
    alignItems: 'center',
    paddingVertical: 16,
    gap: 4,
  },
  avatarRingWrap: {
    position: 'relative',
    marginBottom: 10,
  },
  avatarRing: {
    width: 80,
    height: 80,
    borderRadius: 40,
    overflow: 'hidden',
    borderWidth: 2.5,
    borderColor: T.border,
    boxShadow: '0 0 18px rgba(204,255,0,0.3)',
  } as any,
  avatarImg: {
    width: '100%',
    height: '100%',
  },
  onlineDot: {
    position: 'absolute',
    bottom: 3,
    right: 3,
    width: 13,
    height: 13,
    borderRadius: 99,
    backgroundColor: T.success,
    borderWidth: 2.5,
    borderColor: T.bg,
  },
  userName: {
    color: T.textPrimary,
    fontSize: 20,
    fontWeight: '700',
  },
  userEmail: {
    color: T.textSecondary,
    fontSize: 13,
    marginTop: 2,
  },
  proBadge: {
    backgroundColor: T.accentDim,
    borderWidth: 1,
    borderColor: 'rgba(204,255,0,0.22)',
    borderRadius: 99,
    paddingHorizontal: 14,
    paddingVertical: 5,
    marginTop: 8,
  },
  proBadgeText: {
    color: T.accent,
    fontSize: 12,
    fontWeight: '600',
    letterSpacing: 0.2,
  },

  // Stats
  statsCard: {
    flexDirection: 'row',
    backgroundColor: T.surface,
    borderWidth: 1,
    borderColor: T.border,
    borderRadius: 18,
    paddingVertical: 14,
  },
  statCell: {
    flex: 1,
    alignItems: 'center',
    gap: 3,
  },
  statCellBorder: {
    borderRightWidth: 1,
    borderRightColor: T.border,
  },
  statVal: {
    color: T.accent,
    fontSize: 20,
    fontWeight: '700',
  },
  statLabel: {
    color: T.textSecondary,
    fontSize: 11,
  },

  // Menu card
  menuCard: {
    backgroundColor: T.surface,
    borderWidth: 1,
    borderColor: T.border,
    borderRadius: 18,
    overflow: 'hidden',
  },

  // Menu row
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 14,
    paddingVertical: 13,
    gap: 12,
  },
  menuItemDivider: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: T.borderSubtle,
  },
  menuIconWrap: {
    width: 38,
    height: 38,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  menuLabelWrap: {
    flex: 1,
    gap: 4,
  },
  menuLabel: {
    color: T.textPrimary,
    fontSize: 14,
    fontWeight: '500',
  },

  // MCP sub-row
  mcpRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
  },
  mcpSheetIcon: {
    width: 13,
    height: 13,
    borderRadius: 3,
  },
  mcpLabel: {
    color: T.textSecondary,
    fontSize: 11,
    fontWeight: '500',
  },
  mcpSep: {
    width: 3,
    height: 3,
    borderRadius: 99,
    backgroundColor: T.textMuted,
  },
  mcpConnected: {
    color: T.success,
    fontSize: 11,
    fontWeight: '600',
  },

  // Sign out
  signOutBtn: {
    backgroundColor: 'rgba(239,68,68,0.08)',
    borderWidth: 1,
    borderColor: 'rgba(239,68,68,0.2)',
    borderRadius: 16,
    padding: 14,
    alignItems: 'center',
  },
  signOutText: {
    color: '#EF4444',
    fontSize: 14,
    fontWeight: '600',
  },
});