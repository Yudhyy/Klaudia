import {
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

// ─── Design tokens (matching Klaudia App.html exactly) ────────────────────────
const T = {
  bg: '#161618',
  surface: '#0C0C0E',
  surface2: '#1C1C1F',
  accent: '#CCFF00',
  accentDim: 'rgba(204,255,0,0.15)',
  textPrimary: '#FFFFFF',
  textSecondary: '#A1A1AA',
  textMuted: '#71717A',
  success: '#16A34A',
  border: '#27272A',
};

// ─── SVG Icons (inline via Text — RN doesn't need svg lib for simple shapes) ──
// Using unicode symbols that match the design intent cleanly
// For production, swap with react-native-svg icons

// ─── Menu items data ───────────────────────────────────────────────────────────
const MENU_ITEMS = [
  {
    label: 'Settings',
    iconBg: 'rgba(99,102,241,0.15)',
    iconColor: '#818CF8',
    icon: '⚙',
    isMCP: false,
  },
  {
    label: 'MCP Connection',
    iconBg: 'rgba(22,163,74,0.15)',
    iconColor: '#16A34A',
    icon: '⬡',
    isMCP: true,
  },
  {
    label: 'Activity History',
    iconBg: 'rgba(245,158,11,0.15)',
    iconColor: '#F59E0B',
    icon: '◷',
    isMCP: false,
  },
  {
    label: 'Contact Us',
    iconBg: 'rgba(59,130,246,0.15)',
    iconColor: '#60A5FA',
    icon: '✉',
    isMCP: false,
  },
  {
    label: 'Privacy Policy',
    iconBg: 'rgba(161,161,170,0.1)',
    iconColor: '#A1A1AA',
    icon: '🔒',
    isMCP: false,
  },
];

const STATS = [
  { val: '127', label: 'Receipts' },
  { val: '24',  label: 'This Month' },
  { val: '3',   label: 'Sheets' },
];

// ─── MCP badge row (GSheets + GDrive + Docs icons) ────────────────────────────
function MCPBadges() {
  const badges = [
    { bg: 'rgba(22,163,74,0.2)', label: 'S', color: '#16A34A' },   // Sheets
    { bg: 'rgba(59,130,246,0.2)', label: '▲', color: '#60A5FA' },  // Drive
    { bg: 'rgba(245,158,11,0.2)', label: '≡', color: '#F59E0B' },  // Docs
  ];
  return (
    <View style={styles.mcpRow}>
      {badges.map((b, i) => (
        <View key={i} style={[styles.mcpBadge, { backgroundColor: b.bg }]}>
          <Text style={{ color: b.color, fontSize: 10, fontWeight: '700' }}>{b.label}</Text>
        </View>
      ))}
      <Text style={styles.mcpConnected}>Connected</Text>
    </View>
  );
}

// ─── Single menu row ───────────────────────────────────────────────────────────
function MenuItem({ item }: { item: typeof MENU_ITEMS[0] }) {
  return (
    <Pressable
      style={({ pressed }) => [styles.menuItem, pressed && { opacity: 0.7 }]}
    >
      <View style={[styles.menuIconWrap, { backgroundColor: item.iconBg }]}>
        <Text style={{ color: item.iconColor, fontSize: 16 }}>{item.icon}</Text>
      </View>
      <View style={styles.menuLabelWrap}>
        <Text style={styles.menuLabel}>{item.label}</Text>
        {item.isMCP && <MCPBadges />}
      </View>
      <Text style={styles.chevron}>›</Text>
    </Pressable>
  );
}

// ─── Main screen ──────────────────────────────────────────────────────────────
export default function ProfileScreen() {
  return (
    <View style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
      >
        {/* ── Title ── */}
        <Text style={styles.pageTitle}>Profile</Text>

        {/* ── Avatar block ── */}
        <View style={styles.avatarBlock}>
          <View style={styles.avatarRing}>
            <Image
              source={require('../../assets/avatar.jpg')}
              style={styles.avatarImg}
            />
          </View>
          <Text style={styles.userName}>Ryuuky</Text>
          <Text style={styles.userEmail}>ryuuky@gmail.com</Text>
          <View style={styles.proBadge}>
            <Text style={styles.proBadgeText}>Pro Plan</Text>
          </View>
        </View>

        {/* ── Stats ── */}
        <View style={styles.statsCard}>
          {STATS.map((s, i) => (
            <View
              key={i}
              style={[styles.statCell, i < STATS.length - 1 && styles.statCellBorder]}
            >
              <Text style={styles.statVal}>{s.val}</Text>
              <Text style={styles.statLabel}>{s.label}</Text>
            </View>
          ))}
        </View>

        {/* ── Menu ── */}
        <View style={styles.menuList}>
          {MENU_ITEMS.map((item, i) => (
            <MenuItem key={i} item={item} />
          ))}
        </View>

        {/* ── Sign Out ── */}
        <Pressable
          style={({ pressed }) => [styles.signOutBtn, pressed && { opacity: 0.7 }]}
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
    paddingBottom: 100,
  },

  // Title
  pageTitle: {
    color: T.textPrimary,
    fontSize: 18,
    fontWeight: '700',
    textAlign: 'center',
    paddingTop: 60,
    paddingBottom: 8,
  },

  // Avatar block
  avatarBlock: {
    alignItems: 'center',
    paddingVertical: 20,
    gap: 4,
  },
  avatarRing: {
    width: 80,
    height: 80,
    borderRadius: 40,
    overflow: 'hidden',
    borderWidth: 3,
    borderColor: T.border,
    marginBottom: 10,
    // Glow via shadow (iOS)
    shadowColor: T.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.35,
    shadowRadius: 14,
  },
  avatarImg: {
    width: '100%',
    height: '100%',
    resizeMode: 'cover',
  },
  userName: {
    color: T.textPrimary,
    fontSize: 20,
    fontWeight: '700',
    marginTop: 2,
  },
  userEmail: {
    color: T.textSecondary,
    fontSize: 13,
    marginTop: 2,
  },
  proBadge: {
    backgroundColor: T.accentDim,
    borderWidth: 1,
    borderColor: 'rgba(204,255,0,0.2)',
    borderRadius: 99,
    paddingHorizontal: 14,
    paddingVertical: 4,
    marginTop: 8,
  },
  proBadgeText: {
    color: T.accent,
    fontSize: 12,
    fontWeight: '600',
  },

  // Stats
  statsCard: {
    flexDirection: 'row',
    marginHorizontal: 20,
    marginBottom: 20,
    backgroundColor: T.surface,
    borderWidth: 1,
    borderColor: T.border,
    borderRadius: 18,
    paddingVertical: 14,
  },
  statCell: {
    flex: 1,
    alignItems: 'center',
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
    marginTop: 2,
  },

  // Menu
  menuList: {
    paddingHorizontal: 20,
    gap: 8,
    flexDirection: 'column',
  },
  menuItem: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: T.surface,
    borderWidth: 1,
    borderColor: T.border,
    borderRadius: 16,
    padding: 14,
    gap: 12,
    marginBottom: 8,
  },
  menuIconWrap: {
    width: 38,
    height: 38,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  menuLabelWrap: {
    flex: 1,
  },
  menuLabel: {
    color: T.textPrimary,
    fontSize: 14,
    fontWeight: '500',
  },
  chevron: {
    color: T.textMuted,
    fontSize: 20,
    lineHeight: 22,
  },

  // MCP badges
  mcpRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 5,
  },
  mcpBadge: {
    width: 20,
    height: 20,
    borderRadius: 4,
    alignItems: 'center',
    justifyContent: 'center',
  },
  mcpConnected: {
    color: T.success,
    fontSize: 10,
    fontWeight: '600',
    marginLeft: 2,
  },

  // Sign out
  signOutBtn: {
    marginHorizontal: 20,
    marginTop: 16,
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