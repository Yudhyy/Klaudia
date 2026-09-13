"""Approval binds exact checked proposals and cannot bypass ledger revisions."""

import asyncio

import pytest

from ledger.errors import ApprovalRequiredError, RevisionConflictError
from ledger.store import LedgerStore
from ledger.table_operations import (
    TableAppendProposal,
    prepare_table_append,
    execute_prepared_append,
)
from ledger.approvals import decide_approval
from tests.e2e.capability_cases import seeded_capability_case
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def proposed_append():
    """Prepare one owned record that requires an explicit human decision."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    try:
        async with seeded_capability_case(store, "append") as fixture:
            owner = fixture.case.turns[0].as_user
            table_id = await store.pool.fetchval(
                "SELECT resource_id FROM ledger_resource WHERE sheet_id IN (SELECT sheet_id FROM ledger_sheet WHERE workspace = $1)",
                fixture.workbook_id,
            )
            prepared = await prepare_table_append(
                store.pool,
                owner,
                TableAppendProposal(
                    table_id=table_id,
                    expected_sheet_revision=0,
                    expected_catalogue_revision=1,
                    records=[
                        {
                            "Date": "2026-06-30",
                            "Merchant": "Taxi vendor",
                            "Category": "Transport",
                            "Amount": 185000,
                        }
                    ],
                ),
                require_approval=True,
            )
            yield store, fixture, prepared
    finally:
        await store.close()


async def test_approval_waits_then_executes_once(proposed_append):
    """All execute paths stop before approval and exact approved replay commits once."""
    store, fixture, prepared = proposed_append
    owner = fixture.case.turns[0].as_user
    before = await fixture.observe_state()
    with pytest.raises(ApprovalRequiredError) as pending:
        await execute_prepared_append(store.pool, owner, prepared["operation_ref"])
    assert await fixture.observe_state() == before
    approved = await decide_approval(
        store.pool, owner, pending.value.approval["approval_id"], approve=True
    )
    assert approved["approved"] is True
    receipts = await asyncio.gather(
        *[
            execute_prepared_append(store.pool, owner, prepared["operation_ref"])
            for _ in range(2)
        ]
    )
    assert receipts[0] == receipts[1]
    assert await fixture.observe_state() == fixture.case.turns[0].expect.ledger_state


async def test_changed_revision_cannot_use_old_approval(proposed_append):
    """A human decision never grants permission to apply a stale proposal."""
    store, fixture, prepared = proposed_append
    owner = fixture.case.turns[0].as_user
    await decide_approval(store.pool, owner, prepared["approval_id"], approve=True)
    await store.pool.execute(
        "UPDATE ledger_sheet SET title = title WHERE workspace = $1",
        fixture.workbook_id,
    )
    with pytest.raises(RevisionConflictError):
        await execute_prepared_append(store.pool, owner, prepared["operation_ref"])


async def test_rejected_and_expired_approvals_never_execute(proposed_append):
    """Rejection and expiry remain ledger constraints even for direct execution."""
    store, fixture, prepared = proposed_append
    owner = fixture.case.turns[0].as_user
    await decide_approval(store.pool, owner, prepared["approval_id"], approve=False)
    with pytest.raises(ValueError, match="rejected"):
        await execute_prepared_append(store.pool, owner, prepared["operation_ref"])
    await store.pool.execute(
        "UPDATE ledger_table_operation SET approval_status = 'approved', approved_fingerprint = fingerprint, approval_expires_at = CURRENT_TIMESTAMP - interval '1 second' WHERE user_id = $1 AND idempotency_key = $2",
        owner,
        prepared["operation_ref"],
    )
    with pytest.raises(ValueError, match="expired"):
        await execute_prepared_append(store.pool, owner, prepared["operation_ref"])
