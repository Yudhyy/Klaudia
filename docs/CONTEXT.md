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
`/accounting-policy.md` does not validate jurisdiction, effective dates, entity,
units or accounting correctness. Backend policy applicability checks are not yet
implemented. Existing mem0 records are not imported by these endpoints.
