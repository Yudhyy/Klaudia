import type { JsonScalar, PendingApproval } from './api';

/** Format a PostgreSQL ledger cell for display without assuming a string value. */
export function formatCellValue(value: JsonScalar | undefined): string {
  return value === null || value === undefined ? '' : String(value);
}

/** Merge approval snapshots and events while preserving arrival order. */
export function mergeApprovals(
  current: PendingApproval[],
  incoming: PendingApproval[],
): PendingApproval[] {
  const merged = new Map(current.map((approval) => [approval.approval_id, approval]));
  for (const approval of incoming) {
    merged.set(approval.approval_id, approval);
  }
  return [...merged.values()];
}
