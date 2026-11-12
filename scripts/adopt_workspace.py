"""Assign a legacy ledger workspace to a real user as their spreadsheet.

Pre-tenancy workspaces (e.g. "default", populated by
migrate_sheets_to_ledger.py) are adopted as system-owned (user 0) when the
new schema is applied. This one-shot script hands such a workspace to an
actual user so their chats operate on the existing data instead of a fresh
empty default spreadsheet.

Usage:
    uv run python scripts/adopt_workspace.py --user 1
    uv run python scripts/adopt_workspace.py --user 1 --workspace default --name Utama

Requires: DATABASE_URL (Postgres DSN) in the environment or .env.
"""

import argparse
import asyncio
import os

from dotenv import load_dotenv


load_dotenv()


async def adopt(workspace: str, user_id: int, name: str) -> None:
    from ledger.store import (
        LedgerStore,
        SpreadsheetNotFoundError,
    )

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")

    store = LedgerStore(dsn)
    await store.connect()
    try:
        info = await store.get_spreadsheet(workspace)
        print(
            f"Adopting workspace '{workspace}' "
            f"(currently user {info['userId']}, name '{info['name']}') "
            f"-> user {user_id}, name '{name}'"
        )
        import asyncpg

        try:
            await store.pool.execute(
                "UPDATE ledger_spreadsheet SET user_id = $2, name = $3 "
                "WHERE spreadsheet_id = $1",
                workspace,
                user_id,
                name,
            )
        except asyncpg.UniqueViolationError:
            raise SystemExit(
                f"User {user_id} already has a spreadsheet named '{name}' "
                "(perhaps an auto-provisioned default). Rename or delete it "
                "first, or pass a different --name."
            )
        tabs = [s["title"] for s in await store.list_sheets(workspace)]
        print(f"Done. User {user_id} now owns {len(tabs)} tabs: {tabs}")
    except SpreadsheetNotFoundError:
        raise SystemExit(f"Workspace '{workspace}' does not exist")
    finally:
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hand a legacy ledger workspace to a user"
    )
    parser.add_argument("--user", type=int, required=True, help="Owning user id")
    parser.add_argument(
        "--workspace", default="default", help="Workspace key (default: 'default')"
    )
    parser.add_argument(
        "--name", default="Utama", help="Display name for the spreadsheet"
    )
    args = parser.parse_args()
    asyncio.run(adopt(args.workspace, args.user, args.name))


if __name__ == "__main__":
    main()
