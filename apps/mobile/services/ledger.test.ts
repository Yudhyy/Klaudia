import assert from 'node:assert/strict';
import test from 'node:test';

import { formatCellValue, mergeApprovals } from './ledger.ts';
import type { PendingApproval } from './api.ts';

const firstApproval: PendingApproval = {
  approval_id: 'approval-1',
  action: 'checked_table_append',
  operation_ref: 'prepared:1',
  proposal: { table_id: 'expenses', records: [{ Amount: 12500 }] },
  summary: 'Append 1 record',
  rows_affected: 1,
  columns_affected: 1,
  expires_at: '2026-09-29T12:00:00+00:00',
};

test('formats every ledger scalar without unsafe string assumptions', () => {
  assert.equal(formatCellValue(null), '');
  assert.equal(formatCellValue(12500), '12500');
  assert.equal(formatCellValue(true), 'true');
  assert.equal(formatCellValue('Tunai'), 'Tunai');
});

test('merges approval events without duplicate actions', () => {
  const merged = mergeApprovals(
    [firstApproval],
    [firstApproval, { ...firstApproval, approval_id: 'approval-2', operation_ref: 'prepared:2' }],
  );

  assert.deepEqual(
    merged.map((approval) => approval.approval_id),
    ['approval-1', 'approval-2'],
  );
});
