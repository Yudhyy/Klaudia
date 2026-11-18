# Klaudia

An open-source accounting workspace for running and evaluating AI agents on
financial documents, spreadsheets, and bookkeeping tasks.

Klaudia brings document extraction, financial calculations, and record updates
into one application. Run it on your own infrastructure, connect your models,
and inspect how the agent does the work.

[Get started](#get-started) · [Research](#research-and-evaluation) · [Contribute](CONTRIBUTING.md) · [Roadmap](ROADMAP.md)

## Why Klaudia

Accounting work involves receipts, invoices, tables, and decisions about what
belongs in the books. An extracted amount is useful when you can check its source
and use it in the next step.

Klaudia connects that work through tools that read documents, calculate amounts,
compare records, and check changes before they reach stored tables. The aim is
an accounting workspace where you can review and correct the agent's work.

For developers and researchers, the same application is a place to test models,
fine-tune document extraction, and measure agent behavior on financial tasks.
The application code and workflow evaluation tools are open to inspect and extend.

## What you can work with

Klaudia is in early development. The current application has a Python backend and
an Expo client prototype. These capabilities are available in the backend:

| Task | What Klaudia provides |
| --- | --- |
| Read financial documents | Extract receipt and invoice fields from supported images and PDFs, and retrieve stored pages |
| Calculate amounts | Exact sums, counts, aging, and variance over registered tables |
| Compare records | Reconcile registered tables with saved matching rules and checks on entities and units |
| Update records | Check proposed table changes and native decimal formulas before execution |
| Review changes | Configure approval for supported writes and retain operation records for recovery |

The current application does not yet include a dedicated web spreadsheet,
complete double-entry bookkeeping, month-end close, or country-specific
accounting rules.

## Bring your own LLM

Choose the models behind Klaudia and connect them with your own credentials.
The main agent supports DeepSeek, Google Gemini, and self-hosted models served
through vLLM.

Document extraction has its own model settings. You can use a different model
for receipts and invoices, including Klaudia's fine-tuned Qwen 3.5-4B model.
Set providers, keys, and endpoints in your local `.env` file. See
[.env.template](.env.template) for supported settings.

## Research and evaluation

Klaudia's research includes a Qwen 3.5-4B fine-tune for receipt extraction. In the
reported experiment, Entity F1 increased from 69.93 to 87.02, and digit accuracy
increased from 77.87 to 94.58.

Read the [receipt extraction report](docs/en/05-development/research/fine-tuning/qwen-3.5-4b-receipts.md)
for the comparison table, training diagram, and dataset note. The
[fine-tuned model is on Oxen](https://www.oxen.ai/Laoode/Qwen-3.5-4B-Klaudia).

The repository also includes [evaluation runners and fixtures](tests/e2e/README.md)
and [saved task reports](tests/e2e/outputs) for agent workflows. Results describe
their recorded models and setup.

## Get started

You need Git, Docker with Compose, and access to the model services you configure.
These steps start the backend and its stores.

1. Clone the repository and copy the environment template.

   ```bash
   git clone https://github.com/Yudhyy/Klaudia.git
   cd Klaudia
   cp .env.template .env
   ```

2. Edit `.env`. Set a random `JWT_SECRET` of at least 32 characters. Configure
   the main model, extraction, and guardrail credentials using the template's
   comments. To require approval for supported writes, add
   `MAIN_CHAT_REQUIRE_APPROVAL=true`.

3. Start the services.

   ```bash
   docker compose up --build -d
   ```

   Compose runs database migrations before starting the API and extraction worker.

4. Check the API.

   ```bash
   curl --fail http://localhost:8000/v1/health
   ```

   With the default port, the response contains `"status":"ok"`. Open
   [localhost:8000/docs](http://localhost:8000/docs) for the API reference.
   If you changed `APP_PORT`, use that port instead.

See the [service guide](services/api/README.md) for setup without Docker and the
[MCP guide](services/api/MCP.md) for read access from MCP clients.

## Contribute

You can contribute accounting examples, interface design, model experiments,
code, or documentation. A receipt the model misreads or a task that needs too
many corrections is a useful place to start.

Read [CONTRIBUTING.md](CONTRIBUTING.md), explore the [roadmap](ROADMAP.md), or
pick an [open issue](https://github.com/Yudhyy/Klaudia/issues). Use public or
synthetic financial records when you share an example.

## License

Klaudia is licensed under [AGPL-3.0](LICENSE).
