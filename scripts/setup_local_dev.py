"""Creates a local development super admin.

For working against a bare local Postgres, where Supabase Auth does not exist.
Inserts the auth.users row, lets the trigger create the profile, promotes it to
super_admin, and stores a password hash in `dev_credentials`.

`dev_credentials` is created here rather than in supabase/migrations on
purpose: it must never exist in a real Supabase database.

Usage:
    python scripts/setup_local_dev.py you@example.com
    ADMIN_PASSWORD=... python scripts/setup_local_dev.py you@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import session_scope
from app.routers.local_auth import hash_password

# asyncpg sends each statement separately, so these stay as individual entries
# rather than one multi-statement string.
DEV_TABLE_STATEMENTS = [
    """
    create table if not exists public.dev_credentials (
      user_id       uuid primary key references auth.users(id) on delete cascade,
      password_hash text not null,
      created_at    timestamptz not null default now()
    )
    """,
    """
    comment on table public.dev_credentials is
      'LOCAL DEVELOPMENT ONLY. Never create this in a real Supabase project.'
    """,
]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--first-name", default=None)
    parser.add_argument("--last-name", default=None)
    parser.add_argument("--role", default="super_admin",
                        choices=("super_admin", "admin", "editor", "author"))
    args = parser.parse_args()

    settings = get_settings()

    if settings.is_production:
        print("Refusing to run against a production environment.", file=sys.stderr)
        return 1

    if not settings.local_auth_enabled:
        print(
            "LOCAL_AUTH_ENABLED is not true.\n"
            "This script is only for local development against a bare Postgres.",
            file=sys.stderr,
        )
        return 1

    email = args.email.strip()
    if "@" not in email:
        print(f"'{email}' is not a valid email address (no '@').", file=sys.stderr)
        return 1

    password = os.environ.get("ADMIN_PASSWORD") or getpass(f"Password for {email}: ")
    if not password:
        print("A password is required.", file=sys.stderr)
        return 1

    async with session_scope() as session:
        for statement in DEV_TABLE_STATEMENTS:
            await session.execute(text(statement))

        # auth.users normally belongs to Supabase; locally it is the stub table,
        # which has no unique constraint on email — so look up first rather than
        # relying on ON CONFLICT.
        user_id = (
            await session.execute(
                text("select id from auth.users where lower(email) = lower(cast(:email as text))"),
                {"email": email},
            )
        ).scalar_one_or_none()

        if user_id is None:
            user_id = (
                await session.execute(
                    text(
                        """
                        insert into auth.users (email, raw_user_meta_data)
                        values (cast(:email as text), jsonb_build_object(
                            'first_name', cast(:first_name as text),
                            'last_name',  cast(:last_name as text),
                            'role',       cast(:role as text)))
                        returning id
                        """
                    ),
                    {
                        "email": email,
                        "first_name": args.first_name,
                        "last_name": args.last_name,
                        "role": args.role,
                    },
                )
            ).scalar_one()
            print(f"Created auth user {email}.")
        else:
            print(f"Account already exists ({email}); updating it.")

        # The on_auth_user_created trigger inserts the profile as 'invited'.
        # Promote it — an account that cannot act is no use as an administrator.
        await session.execute(
            text(
                """
                update public.profiles
                set role = cast(:role as public.user_role),
                    status = 'active',
                    can_publish = true,
                    first_name = coalesce(cast(:first_name as text), first_name),
                    last_name  = coalesce(cast(:last_name as text), last_name)
                where user_id = cast(:user_id as uuid)
                """
            ),
            {
                "role": args.role,
                "first_name": args.first_name,
                "last_name": args.last_name,
                "user_id": user_id,
            },
        )

        await session.execute(
            text(
                """
                insert into public.dev_credentials (user_id, password_hash)
                values (:user_id, :hash)
                on conflict (user_id) do update set password_hash = excluded.password_hash
                """
            ),
            {"user_id": user_id, "hash": hash_password(password)},
        )

        role = (
            await session.execute(
                text("select role::text from public.profiles where user_id = :u"),
                {"u": user_id},
            )
        ).scalar_one()

    print(f"Local account ready: {email} ({role}, active, publishing enabled)")
    print("Sign in at http://localhost:5173/admin/login")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
