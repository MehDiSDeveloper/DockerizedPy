"""Set a member's app-wide role from the command line.

The bootstrap problem this solves: ``PATCH /users/{id}/role`` requires an
admin, and a fresh database has none -- so the *first* admin cannot be made
through the app. That is deliberate. Promotion is an operator action with
shell access, not a self-service flow, and there is no "first signup becomes
admin" rule: on a database that already has members it would hand the panel
to whoever registered first, and on an empty one it makes the role depend on
signup order rather than on someone deciding.

Usage (the venv, as everything else in this repo)::

    .venv\\Scripts\\python -m app.scripts.set_user_role someone@example.com admin
    .venv\\Scripts\\python -m app.scripts.set_user_role someone@example.com member
    .venv\\Scripts\\python -m app.scripts.set_user_role --list

Every subsequent role change belongs in the panel -- this exists for the
first one, and for the day the only admin's account is gone.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User, UserRole

ROLES = [role.value for role in UserRole]


async def _list_members() -> int:
    async with AsyncSessionLocal() as session:
        members = (
            (await session.execute(select(User).order_by(User.id))).scalars().all()
        )
    if not members:
        print("no members yet")
        return 0
    for member in members:
        print(f"  {member.id:>4}  {member.role:<9} {member.email or '-':<32} {member.name}")
    return 0


async def _set_role(email: str, role: str) -> int:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            print(f"no member with email {email!r}", file=sys.stderr)
            return 1
        previous = user.role
        user.role = role
        await session.commit()
    print(f"{email}: {previous} -> {role}")
    return 0


def main() -> int:
    # Member names are Farsi, and a Windows console defaults to cp1252 --
    # printing one there raises UnicodeEncodeError and the tool dies on the
    # listing it exists to show. Replace rather than fail: a mangled glyph
    # still identifies the row, a traceback does not.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email", nargs="?", help="the member's email address")
    parser.add_argument("role", nargs="?", choices=ROLES, help="the role to give them")
    parser.add_argument(
        "--list", action="store_true", help="list every member and their role"
    )
    args = parser.parse_args()

    if args.list:
        return asyncio.run(_list_members())
    if not args.email or not args.role:
        parser.error("give an email and a role, or --list")
    return asyncio.run(_set_role(args.email, args.role))


if __name__ == "__main__":
    sys.exit(main())
