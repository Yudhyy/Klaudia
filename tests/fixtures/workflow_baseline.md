# Workflow compatibility fixture

`workflow_baseline.json` freezes the task input and pending agent checkpoint
produced by `MainChatService` at
`0a6ac001a990eb8aceb98ffdcba359410ddb1b28`, before the workflow boundary change.
The capture loaded that file from Git and used the unchanged agent runtime.

The run used a scripted `execute_operation` call and a supplied executor that
raised `ConnectionError` before returning a receipt. A recording task store
saved the original input, checkpoint and failed status. All identities are
synthetic. No model provider or database received a request during capture.

The replay test reads the saved file. It must execute the original pending
reference before new model work, retain the original input and task identity,
and return the supplied receipt. Do not regenerate the fixture from changed
code to fix a replay failure. Existing agent compatibility database fixtures
cover exact approvals, real committed receipts and unchanged financial state.
