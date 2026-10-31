# Agent compatibility fixtures

These files freeze the runtime at commit
`2cfd672c7a3673f3465ebbee5619ea755734ce0d`, the merge of PR #32 into
`development`. They protect the capability changes tracked in issue #3.
Production source was unchanged during capture. `v1` names the checkpoint
format, not a new package release.

Do not update these files from the current implementation to fix a failing
test. Keep this baseline during contract moves. A deliberate contract change
needs a reviewed upgrade path and a separate fixture set. Tests read saved
files; they do not generate expected values from the code under test.

## Saved contracts

- `runtime.json` records the source commit, installed schema-library versions,
  complete argument schemas and model-facing tool definitions for all 17 tools.
  It keeps titles, descriptions, defaults, union tags and `$defs`. Each of the
  32 optional input combinations records tool binding order, its selected prompt
  and its checkpoint identity. Authoring without operations adds no tools.
- `read_prompt.txt` and `append_prompt.txt` contain the exact UTF-8 prompt bytes.
  The identity inputs include user context, the original message and run limits.
  Runtime identity hashes the sorted JSON object containing context, message,
  prompt, argument schemas and limits. Tool descriptions have separate assertions
  because the runtime does not put them in this hash.
- `skills.json` keeps ordered skill names, versions and descriptions. `skills/`
  contains the eight exact packaged Markdown files. Tests compare both installed
  bytes and the registry's loaded content.
- `requests.json` records original inputs and serialized JSON with defaults for
  every tool, including all five table-authoring actions. Typed edits retain
  decimal text and explicit formula rounding.
- `errors.json` records model-visible overlap, access, revision, policy, value,
  invalid-argument and unknown-tool failures. It includes the bounded value-error
  text and the tool message status.
- `evidence.json` records document pagination, memory trust fields, policy scope,
  exact decimal text and the checked financial request sent to the backend.
  These tool tests use fixed store responses. They do not qualify financial
  arithmetic; the ledger tests own that check.

## Stored continuation

`continuation.json` came from a real checked append in the isolated PostgreSQL
sandbox. It contains an original version-1 checkpoint and two sets of persisted
rows: approval pending, and commit before checkpoint save. The checkpoint keeps
the loaded skill, inspected resource, prior tool evidence, counters and original
pending `execute_operation` call. Its identity is the captured hash, not a hash
recomputed by the replay test.

Each set contains the owned workbook, sheet, registered table, operation and task.
The rows use current schema columns; the generated table-search vector and an
obsolete sandbox-only operation column are omitted. The task's checkpoint and
input payload remain their original JSON strings. The ledger row retains the
original request JSON, fingerprint, approval state and receipt. Approval state
belongs to ledger storage, not to the agent checkpoint.

The capture set approval expiry to `2099-01-01T00:00:00Z` before the first run so
the approval fixture stays valid. All identities and timestamps are synthetic
test values. The integration fixture inserts the saved rows directly. It does
not prepare a replacement operation or regenerate a checkpoint. Pending approval
must pause without a model call or cell change. Approval then commits the saved
proposal. A lost-checkpoint replay must return the exact saved receipt and keep
the committed grid and revision unchanged.

Run the focused checks from the repository root:

```sh
uv run pytest tests/unit/test_agent_compatibility.py tests/unit/test_agent_payload_compatibility.py tests/unit/test_agent_continuation_compatibility.py -q
uv run pytest tests/integration/database/test_agent_compatibility.py -q
```

The second command uses the isolated database described in
`tests/integration/postgres.py` and resets application test tables through the
shared integration fixture. Inspect the actual database target before running
it. Neither command calls a model provider.
