# Document intelligence

Document contracts, inline ingestion, and queued page processing. The package
uses the Python standard library. Callers supply document and outcome storage,
object storage, cache, OCR, native media processing, and queue operations.

The ingest service checks upload names, types, sizes, and page counts. It
normalizes extraction facts to the stored schema and applies the existing cache
and outcome rules. Queued processing resolves each page through the owned
outcome store and saves its result before it sends a progress notification.

Application adapters bind the package to PostgreSQL, Redis, object storage,
Taskiq, OCR providers, and native image and PDF libraries. The API keeps its
attachment and error formats. The worker keeps its existing job payload and
retry configuration.
