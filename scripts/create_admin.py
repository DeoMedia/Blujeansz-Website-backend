"""Creates a CMS user and promotes them to super_admin.

Bootstraps the first administrator. The schema deliberately prevents anyone
granting themselves privileges, so the first super_admin cannot be created
through the CMS itself — this script does it server-side with the service-role
key instead.

Credentials are never hardcoded and never printed. The password is read from a
hidden prompt, or from ADMIN_PASSWORD if you need it non-interactive (in CI,
for example).

Usage:
    python scripts/create_admin.py you@example.com
    python scripts/create_admin.py you@example.com --role admin
    python scripts/create_admin.py you@example.com --reset-password
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from getpass import getpass
from pathlib import Path

# Allow `python scripts/create_admin.py` from the repo root, not just `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import session_scope
from app.models import Profile

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_ROLES = ("super_admin", "admin", "editor", "author")


def check_password(password: str) -> list[str]:
    """Supabase enforces a minimum length; the rest is our own guard against a
    throwaway password on an account that can publish to a live site."""
    problems = []
    if len(password) < 12:
        problems.append("at least 12 characters")
    if not re.search(r"[a-z]", password):
        problems.append("a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("a symbol")
    return problems


async def find_auth_user(client: httpx.AsyncClient, base: str, key: str, email: str):
    """Supabase has no direct get-user-by-email, so page through the list."""
    response = await client.get(
        f"{base}/auth/v1/admin/users",
        params={"page": 1, "per_page": 200},
        headers={"Authorization": f"Bearer {key}", "apikey": key},
    )
    response.raise_for_status()
    users = response.json().get("users", [])
    return next((u for u in users if (u.get("email") or "").lower() == email.lower()), None)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--role", default="super_admin", choices=VALID_ROLES)
    parser.add_argument("--first-name", default=None)
    parser.add_argument("--last-name", default=None)
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Set a new password if the account already exists.",
    )
    parser.add_argument(
        "--allow-weak-password",
        action="store_true",
        help="Proceed even if the password fails the strength check.",
    )
    args = parser.parse_args()

    email = args.email.strip()
    if not EMAIL_RE.match(email):
        print(f"'{email}' is not a valid email address.", file=sys.stderr)
        if "@" not in email:
            print("It is missing an '@'.", file=sys.stderr)
        return 1

    settings = get_settings()
    if not settings.supabase_service_role_key:
        print(
            "SUPABASE_SERVICE_ROLE_KEY is not set.\n"
            "Add it to .env (Supabase dashboard -> Project Settings -> API).",
            file=sys.stderr,
        )
        return 1

    password = os.environ.get("ADMIN_PASSWORD") or getpass(f"Password for {email}: ")
    problems = check_password(password)
    if problems:
        # This account can publish to the live site, so weak credentials are
        # worth objecting to — but it is the operator's account and their call,
        # hence an override rather than a refusal.
        message = "This password is missing " + ", ".join(problems) + "."
        if args.allow_weak_password:
            print(f"Warning: {message} Continuing because --allow-weak-password was given.")
        else:
            print(message, file=sys.stderr)
            print("Use a stronger one, or pass --allow-weak-password to proceed.", file=sys.stderr)
            return 1

    base = settings.supabase_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "apikey": settings.supabase_service_role_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        existing = await find_auth_user(client, base, settings.supabase_service_role_key, email)

        if existing:
            user_id = existing["id"]
            print(f"Account already exists ({email}).")
            if args.reset_password:
                response = await client.put(
                    f"{base}/auth/v1/admin/users/{user_id}",
                    json={"password": password, "email_confirm": True},
                    headers=headers,
                )
                response.raise_for_status()
                print("Password updated.")
        else:
            response = await client.post(
                f"{base}/auth/v1/admin/users",
                json={
                    "email": email,
                    "password": password,
                    # Confirm immediately: this account is created deliberately
                    # by an operator, so there is no invitation email to wait on.
                    "email_confirm": True,
                    "user_metadata": {
                        "first_name": args.first_name,
                        "last_name": args.last_name,
                        "role": args.role,
                    },
                },
                headers=headers,
            )
            if response.status_code >= 400:
                print(f"Supabase refused: {response.text[:300]}", file=sys.stderr)
                return 1

            user_id = response.json()["id"]
            print(f"Created auth user {email}.")

    # The on_auth_user_created trigger inserts the profile as 'invited'. Promote
    # it: an account that cannot act is not much use as the first administrator.
    async with session_scope() as session:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_id))
        ).scalar_one_or_none()

        if profile is None:
            print(
                "No profile row was created. Check that migration 0001 ran and the\n"
                "on_auth_user_created trigger exists on auth.users.",
                file=sys.stderr,
            )
            return 1

        profile.role = args.role
        profile.status = "active"
        profile.can_publish = True
        if args.first_name:
            profile.first_name = args.first_name
        if args.last_name:
            profile.last_name = args.last_name

    print(f"Profile set to {args.role}, status active, publishing enabled.")
    print("\nSign in at /admin/login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
