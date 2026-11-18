# Klaudia roadmap

The next milestone is a working web demo. Version 1 builds on that demo with a
complete, limited bookkeeping cycle. Version 2 extends the cycle into period
close and regional accounting support.

These are development targets, not released capabilities or delivery dates.
Each stage needs a runnable example and results that another person can check.

| Stage | Target | Status |
| --- | --- | --- |
| Web demo | Complete one financial task in the browser, with source review and saved output | Next |
| v1 | Turn documents into reviewed records, balanced journal entries, and a trial balance | Planned |
| v2 | Repeat the work across periods, with close controls and defined regional rules | Proposed |
| Accounting benchmark | Publish reusable accounting tasks and evaluation methods | Later research |

## Before v1: a working web demo

The first demo should let a person load financial documents, inspect extracted
fields beside the source, correct records in a spreadsheet, and save the result.
The exact task and input formats need a design before implementation.

The demo needs these parts:

- A public or synthetic sample with expected records and known exceptions.
- A browser interface for source documents, extracted fields, and spreadsheet work.
- A review step that shows proposed changes and unresolved differences.
- Saved progress and an exported result that the user can inspect outside Klaudia.
- Setup instructions that let someone run the same sample with a supported model.

The spreadsheet should run without an Excel or Google Sheets service.
IronCalc is the candidate engine. Its integration needs checks for formulas,
XLSX exchange, numeric behavior, and workbook limits. The current backend uses
native decimal formulas. The [IronCalc evaluation](docs/IRONCALC_CAPABILITIES.md)
and [formula reference](docs/TYPED_FORMULAS.md) describe the existing work.

The demo is ready when another person can run the sample, correct an extraction
error, review changes, and reopen the saved result. Document supported file sizes,
table sizes, and failure cases before expanding the workflow.

## v1: a usable accounting workspace

Version 1 targets one complete bookkeeping cycle for one entity, period, and
currency. Users should be able to follow a financial record from its source
document through review, posting, reconciliation, and export.

| Capability | Expected result |
| --- | --- |
| Document entry | Supported receipts and invoices become editable records with source references |
| Spreadsheet work | Users inspect and edit records with documented formula and XLSX support |
| Double-entry bookkeeping | A chart of accounts, balanced journal entries, reversals, and a trial balance follow explicit posting rules |
| Reconciliation | Users see matched records, differences, and items that still need review |
| Model choice | Users can configure supported hosted or self-hosted models for the agent and extraction |
| Self-hosting | Setup, upgrades, backups, and recovery have documented checks |

Spreadsheet edits must follow the same posting rules as agent actions. An
interrupted task must not duplicate a posting or hide unfinished work.

The release needs a repeatable sample for the whole cycle, tests for accounting
rules, and published evaluation results with known failures. Document access
controls and measured workload limits before describing a deployment as ready
for production.

## v2: period close and regional support

Version 2 builds on a working v1 cycle. Its proposed scope includes adjustments,
close checklists, period locks, reopening rules, reviewer roles, and recurring
imports. Broader spreadsheet support follows the formats that these tasks need.

Regional support has several parts. Language and date formats differ from
currency conversion, accounting rules, and tax requirements. Indonesia, the
United States, and individual European jurisdictions need separate, defined
profiles with sources, examples, tests, and domain review.

A profile must state what it supports before release. A country or currency code
alone does not establish accounting support. Close and regional work need their
own design and acceptance checks after the v1 workflow is stable.

## Research alongside the product

The first spreadsheet evaluations use
[SpreadsheetBench 2](https://arxiv.org/abs/2606.29955) and
[MBABench](https://arxiv.org/abs/2605.22664). Establish a baseline, inspect failures,
and improve Klaudia before developing a separate accounting benchmark.
Klaudia has no published results on either benchmark yet.

Keep document extraction, spreadsheet tasks, and accounting workflow checks
separate. The [Qwen receipt extraction report](docs/en/05-development/research/fine-tuning/qwen-3.5-4b-receipts.md)
records earlier fine-tuning work. Further experiments can compare models,
training methods, and agent behavior against fixed evaluation cases.

Each new report should identify model, dataset, evaluator, engine, and code
versions. Include failed and skipped cases, runtime, and the work needed to
correct the output. Label partial or adapted benchmark runs. Keep training
examples separate from held-out evaluation cases.

A later accounting benchmark can use the workflows and failures found through
this work. Publishing it requires reusable tasks, clear data rights, expected
outputs, and an evaluator that others can run.

## Where to contribute

Start with a problem that someone else can reproduce. These areas connect
contributions to the release targets:

| Area | Useful first contribution | Stage |
| --- | --- | --- |
| Accounting examples | A sample task with expected records and exceptions | Web demo |
| Interface design | A flow for source review, field correction, or spreadsheet editing | Web demo |
| Spreadsheet engine | A compatibility test for a formula or workbook feature | Web demo and v1 |
| Models and extraction | A receipt failure case or a measured model comparison | Research and v1 |
| Evaluation | A benchmark adapter or a report that explains failed tasks | Research |
| Accounting core | Posting, reversal, or reconciliation rules with behavior tests | v1 |
| Operations | A reproducible setup, upgrade, backup, or recovery check | v1 |
| Close and regions | A sourced workflow with examples and explicit limits | v2 |

Check the [open issues](https://github.com/Yudhyy/Klaudia/issues) and
[contribution guide](CONTRIBUTING.md) before starting a larger change.
