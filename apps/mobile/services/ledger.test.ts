import assert from 'node:assert/strict';
import test from 'node:test';

import { formatCellValue, mergeApprovals } from './ledger.ts';
import type { PendingApproval } from './api.ts';

const firstApproval: PendingApproval = {
  approval_id: 'approval-1',
  action: 'delete_sheet',
  sheet: 'Jun',
  summary: 'Delete Jun',
  rows_affected: 35,
  columns_affected: 7,
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
    [firstApproval, { ...firstApproval, approval_id: 'approval-2', sheet: 'Mei' }],
  );

  assert.deepEqual(
    merged.map((approval) => approval.approval_id),
    ['approval-1', 'approval-2'],
  );
});
