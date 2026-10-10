"""User management from the command line (on the collector host or
container, with DATABASE_URL set):

    python -m app.users create-user alice [--admin]
    python -m app.users set-password alice

Passwords are read from the terminal (or REQLY_NEW_PASSWORD), never taken
as arguments, so they don't end up in shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

import asyncpg

from ..config import settings
from . import accounts


def _password() -> str:
    from_env = os.environ.get("REQLY_NEW_PASSWORD")
    if from_env:
        return from_env
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat it: "):
        sys.exit("passwords don't match")
    return first


async def _run(args) -> None:
    dsn = settings.database_url.replace("postgres://", "postgresql://", 1)
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=1)
    try:
        if args.command == "create-user":
            user = await accounts.create_user(pool, args.username, _password(), is_admin=args.admin)
            print(f"created {'admin ' if user['is_admin'] else ''}user {user['username']!r}")
        else:
            await accounts.set_password(pool, args.username, _password())
            print(f"password set for {args.username!r}; their sessions were signed out")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.users")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    reset = sub.add_parser("set-password")
    reset.add_argument("username")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except accounts.AuthError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
