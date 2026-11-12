#!/usr/bin/env bash
# Extraction worker entrypoint.
#
# Each worker process pulls extract_page_task off Redis and calls vLLM (or the
# mock OCR fixture in dev). Default concurrency is 8: vLLM gates real
# parallelism via continuous batching, so worker processes are network-bound
# and we get more throughput from many in-flight asyncio tasks per process
# than from many processes. Tune --workers + --concurrency to match GPU
# capacity (see docs/PRD.md sizing notes).

set -euo pipefail

cd "$(dirname "$0")/.."

WORKER_COUNT="${WORKER_COUNT:-1}"
CONCURRENCY="${WORKER_CONCURRENCY:-8}"

# Importing klaudia_api.composition.extraction_worker registers the
# WORKER_STARTUP/SHUTDOWN hooks that build per-worker DB/Redis/MinIO/OCR.
exec uv run taskiq worker \
    klaudia_api.interfaces.workers.broker:broker \
    klaudia_api.interfaces.workers.extraction_worker \
    klaudia_api.composition.extraction_worker \
    --workers "$WORKER_COUNT" \
    --max-async-tasks "$CONCURRENCY" \
    --log-level INFO
