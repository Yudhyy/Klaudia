import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Colors, Radius } from '../../constants/theme';
import type { ApprovalDecision, PendingApproval } from '../../services/api';

type ApprovalCardProps = {
  approval: PendingApproval;
  ledgerName: string;
  resolving: boolean;
  disabled: boolean;
  onDecision: (approvalId: string, decision: ApprovalDecision) => void;
};

export function ApprovalCard({
  approval,
  ledgerName,
  resolving,
  disabled,
  onDecision,
}: ApprovalCardProps): React.JSX.Element {
  return (
    <View style={styles.card}>
      <Text style={styles.label}>APPROVAL REQUIRED</Text>
      <Text style={styles.summary}>{approval.summary}</Text>
      <Text style={styles.target}>Ledger: {ledgerName}</Text>
      <Text style={styles.target}>Sheet: {approval.sheet ?? 'Ledger structure'}</Text>
      <Text style={styles.target}>Action: {approval.action}</Text>
      <Text style={styles.impact}>
        {approval.rows_affected} rows, {approval.columns_affected} columns
      </Text>
      <View style={styles.actions}>
        <Pressable
          style={({ pressed }) => [
            styles.rejectButton,
            pressed && styles.pressed,
            disabled && styles.disabled,
          ]}
          onPress={() => onDecision(approval.approval_id, 'reject')}
          disabled={disabled}
        >
          <Text style={styles.rejectText}>Reject</Text>
        </Pressable>
        <Pressable
          style={({ pressed }) => [
            styles.approveButton,
            pressed && styles.pressed,
            disabled && styles.disabled,
          ]}
          onPress={() => onDecision(approval.approval_id, 'approve')}
          disabled={disabled}
        >
          <Text style={styles.approveText}>{resolving ? 'Processing...' : 'Approve'}</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: 6,
    marginHorizontal: 12,
    marginVertical: 6,
    padding: 14,
    borderWidth: 1,
    borderColor: '#7F1D1D',
    borderRadius: Radius.card,
    backgroundColor: '#231313',
  },
  label: {
    color: '#FCA5A5',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.4,
  },
  summary: {
    color: Colors.textPrimary,
    fontSize: 14,
    fontWeight: '600',
  },
  impact: {
    color: Colors.textSecondary,
    fontSize: 12,
  },
  target: {
    color: Colors.textPrimary,
    fontSize: 12,
  },
  actions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
    marginTop: 6,
  },
  rejectButton: {
    paddingHorizontal: 14,
    paddingVertical: 9,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: 10,
  },
  approveButton: {
    paddingHorizontal: 14,
    paddingVertical: 9,
    borderRadius: 10,
    backgroundColor: '#F87171',
  },
  rejectText: {
    color: Colors.textSecondary,
    fontSize: 12,
    fontWeight: '600',
  },
  approveText: {
    color: '#1F1111',
    fontSize: 12,
    fontWeight: '700',
  },
  pressed: {
    opacity: 0.7,
  },
  disabled: {
    opacity: 0.45,
  },
});
