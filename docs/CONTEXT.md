# Human context documents

Klaudia stores explicit human-authored context in PostgreSQL, separate from
financial records, the resource catalogue and task history. The main agent can
read these documents on demand. It cannot edit them.

The supported paths are `/preferences.md`, `/conventions.md` and
`/accounting-policy.md`. Each document holds at most 8192 UTF-8 bytes. An optional
source note holds at most 512 characters. The API rejects null bytes in both.
Source notes describe the editor's stated source; they are not verified provenance.
The server records the authenticated actor, timestamp and revision.

## API

All requests require the usual bearer token. Identity comes from that token;
request bodies cannot select another owner. Use these routes:

| Method | Route | Behaviour |
| --- | --- | --- |
| GET | `/v1/memory/preferences.md` | Read current content, revision and status |
| PUT | `/v1/memory/preferences.md` | Create, replace or restore at an expected revision |
| DELETE | `/v1/memory/preferences.md?expected_revision=1` | Hide current content and record a tombstone |

Replace `preferences.md` with either of the other supported filenames. A PUT body
looks like this:

```json
{
  "expected_revision": 0,
  "content": "Show monetary totals in their recorded currency.",
  "source_note": "Explicit user preference"
}
```

Revision zero means the document has never existed. GET returns a `missing`
document at revision zero when no row exists. A successful edit returns `active`
content with a higher revision. Read before updating; conflicting edits return
`409`. Unsupported paths and invalid bodies return `422`.

DELETE requires a positive observed revision. It returns `deleted`, a new revision
and null content. To restore content, PUT with the tombstone's revision. Revision
zero cannot overwrite a deleted document. Concurrent edits against the same
revision have at most one winner.

## Retention and limits

Current state and revision history commit in one transaction. Deletion hides the
current content but retains earlier content in history. It does not erase text
already stored in conversations, traces or backups. The API does not currently
expose historical revisions or provide a data-erasure operation.

The agent's `read_memory_document` tool accepts only an allowed path. It returns
the observed revision and status, labels content as untrusted context, and does
not add write authority. A database failure is not treated as a missing document.

Preferences and policy prose cannot replace live ledger facts. Reading
`/accounting-policy.md` alone does not validate applicability. The separate
`reconcile_with_policy` tool performs the bounded checks below. Context edits require an explicit authenticated request.


## Structured reconciliation policy

PUT `/v1/memory/accounting-policy.md` with human-readable content and explicit
`policy` metadata. Only this path accepts policy metadata. This example declares
application rules, not statutory accounting requirements:

```json
{
  "expected_revision": 0,
  "content": "Reconcile invoice amounts using the approved rules below.",
  "source_note": "Explicit owner decision",
  "policy": {
    "entity": "Example Ltd",
    "jurisdiction": "owner-declared-jurisdiction",
    "effective_from": "2026-01-01",
    "effective_until": "2026-12-31",
    "unit": "USD",
    "numeric_text": "decimal",
    "null_amounts": "reject",
    "null_keys": "reject",
    "tolerance": "0.01"
  }
}
```

Tolerance must be exact decimal text, nonnegative, with at most 64 digits and
32 decimal places. Dates must use `YYYY-MM-DD`; both ends are inclusive.
Numeric text can be `reject` or `decimal`; blank amounts can be `reject` or
`exclude`; null keys can be `reject` or `never_match`. Invalid numeric operands
always reject. Entity, jurisdiction and unit labels must be nonblank and unpadded.

Prose and metadata commit together with revision history. A replacement PUT that
omits `policy` clears existing structured metadata. DELETE clears current prose
and metadata while retaining both in history. Older prose-only documents remain
readable, but cannot authorise policy-backed reconciliation.

The main agent loads the versioned `policy-reconciliation` procedure and calls
`reconcile_with_policy`. The backend requires:

- An active structured policy at the supplied observed revision.
- An entity and jurisdiction matching the saved policy, and an as-of date within
  its validity period. Jurisdiction is caller-declared, not independently verified.
- Both registered tables to declare exactly the policy entity.
- Explicit unit columns on both sources, with every record matching the saved unit.
- Current ownership and the same source/catalogue revisions used to check entities.
- An unchanged policy revision when calculation finishes.

The tool supplies tolerance and parsing/null rules from saved metadata. It compares
all registered rows by unique exact keys using left-minus-right differences. It
applies no rounding or currency conversion. The as-of date selects policy validity;
it does not filter transaction rows. Pagination limits displayed records, not the
comparison population. Missing sides remain distinct from zero; excluded blank
amounts remain counted. Duplicate keys reject rather than silently aggregate.

Results carry source revisions and `policy_evidence`, including the complete
applied parameters and scope limits. `accounting_validation=policy_parameters_checked`
is not a legal-compliance or full-accounting certification. Evidence describes the
observed revision; policy may change after the response. A task replay rejects
changed policy or lost source ownership. Stale-policy HTTP retries return 409 and
require a new task after the user resolves the policy change.

The main agent's generic `financial_query` rejects reconciliation to prevent a
policy bypass. Other generic financial operations still use their existing explicit
query rules; this release does not claim saved-policy enforcement for them. The
raw ledger financial API remains a deterministic calculation interface.

## Verification

Run these checks against the isolated PostgreSQL test database, never a user store:

```bash
uv run pytest tests/unit/test_accounting_policy.py tests/unit/test_policy_reconciliation.py tests/unit/test_policy_route_conflicts.py tests/integration/database/test_memory_documents.py tests/integration/database/test_policy_reconciliation.py tests/integration/api/test_memory_routes.py -q
```

These checks cover policy bounds, exact storage, applicability rejection, source
races, units, owner isolation, task replay and revision conflicts.
They do not establish live-model reliability or legal correctness.
