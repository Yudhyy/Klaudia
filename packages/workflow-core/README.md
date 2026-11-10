# Workflow core

Task storage, progress and human-decision interfaces with task start/resume
coordination. The package uses only the Python standard library.

`WorkflowService` receives a `TaskStore` and a function that binds an acquired
session to a `TaskRun`. Storage supplies exclusive task access. The supplied run
reads the original saved input and returns a `TaskCompletion`; workflow records
its observed status and returns its value. A run failure records `failed` before
it reads supplied recovery evidence. Cancellation records `interrupted` when
storage permits, then propagates the original exception.

The application owns PostgreSQL, locks, source-access checks, agent continuation
formats and run construction. Ledger adapters own financial validation, approval
execution and atomic effects/receipts. Workflow treats saved state as opaque and
creates no pools, clients or background processes. Existing application workflow
imports remain aliases to these canonical definitions.
