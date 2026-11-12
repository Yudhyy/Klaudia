"""Purge a receipt's dedup/extraction footprint so a cache-MISS case is
reproducible across runs.

The cache-miss test (KIE01, 003-receipt) only measures a miss the FIRST time the
image is ingested; after that the page's extraction lives in three places:

    L1  Redis    extract:u<uid>:<page_blake3>
    L2  Postgres blob_extraction(user_id, page_blake3)
    blob MinIO   blobs|pages/u<uid>/<shard>/<hash>.<ext>

This helper deletes all three (plus the file_blob / file_blob_page /
metadata_file rows) for a given file_name + user, so the next ingest re-runs KIE
and reports cache_misses=1 again. The E2E harness calls it BEFORE every
`cache_miss`-tagged case (idempotent → a true miss on every run); it can also be
run standalone to clean the DB on demand:

    uv run python -m tests.e2e.kie_reset 003-receipt.png
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def purge_kie_file(container: Any, user_id: int, file_name: str) -> int:
    """Remove all dedup/extraction artifacts for (user_id, file_name).

    Returns the number of distinct page hashes whose cache was cleared.
    Fail-soft: each external delete (Redis, MinIO) is best-effort so a missing
    backend never breaks the purge of the others.
    """
    db = container.db_client
    files = await db.fetchall(
        "SELECT id FROM metadata_file WHERE user_id = $1 AND file_name = $2",
        (user_id, file_name),
    )
    if not files:
        return 0
    file_ids = [f["id"] for f in files]

    blob_ids: set[int] = set()
    for fid in file_ids:
        for row in await db.fetchall(
            "SELECT blob_id FROM metadata_file_blob WHERE metadata_file_id = $1",
            (fid,),
        ):
            blob_ids.add(row["blob_id"])

    page_hashes: set[str] = set()
    minio_keys: set[str] = set()
    for bid in blob_ids:
        for row in await db.fetchall(
            "SELECT page_blake3, page_minio_key FROM file_blob_page WHERE blob_id = $1",
            (bid,),
        ):
            page_hashes.add(row["page_blake3"])
            if row["page_minio_key"]:
                minio_keys.add(row["page_minio_key"])
        blob = await db.fetchone(
            "SELECT minio_key FROM file_blob WHERE blob_id = $1", (bid,)
        )
        if blob and blob["minio_key"]:
            minio_keys.add(blob["minio_key"])

    cache = getattr(container, "dedup_cache", None)
    for h in page_hashes:
        await db.execute(
            "DELETE FROM blob_extraction WHERE user_id = $1 AND page_blake3 = $2",
            (user_id, h),
        )
        if cache is not None and hasattr(cache, "delete_extraction"):
            try:
                await cache.delete_extraction(user_id, h)
            except Exception as exc:
                logger.warning("redis delete_extraction failed for %s: %s", h[:12], exc)

    # MinIO objects.
    store = getattr(container, "object_store", None)
    if store is not None:
        for key in minio_keys:
            try:
                await store.delete(key)
            except Exception as exc:
                logger.warning("minio delete failed for %s: %s", key, exc)

    # Blob + file rows (so the next upload is a genuinely fresh insert).
    for bid in blob_ids:
        await db.execute("DELETE FROM file_blob_page WHERE blob_id = $1", (bid,))
        await db.execute("DELETE FROM file_blob WHERE blob_id = $1", (bid,))
    for fid in file_ids:
        await db.execute(
            "DELETE FROM metadata_file_blob WHERE metadata_file_id = $1", (fid,)
        )
        await db.execute("DELETE FROM pages WHERE metadata_file_id = $1", (fid,))
        await db.execute("DELETE FROM metadata_file WHERE id = $1", (fid,))

    logger.info(
        "purged KIE artifacts for %s (user %d): %d page hash(es), %d blob(s)",
        file_name,
        user_id,
        len(page_hashes),
        len(blob_ids),
    )
    return len(page_hashes)


async def _main(file_names: list[str], user_id: int) -> None:
    from klaudia_api.composition.api import KlaudiaContainer
    from klaudia_api.composition.settings import get_settings

    container = await KlaudiaContainer.create(get_settings())
    try:
        for name in file_names:
            n = await purge_kie_file(container, user_id, name)
            print(f"purged {name}: {n} page hash(es) cleared")
    finally:
        await container.shutdown()


if __name__ == "__main__":
    import asyncio
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    args = sys.argv[1:] or ["003-receipt.png"]
    asyncio.run(_main(args, user_id=1))
