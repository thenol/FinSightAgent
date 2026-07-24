"""Administrative provisioning commands for local and deployment setup."""

import argparse
import getpass
import sys
from datetime import datetime, timezone

from app.api.auth import PASSWORD_HASH
from app.domain import User
from app.platform.ids import new_id
from app.platform.repository import SqlAlchemyRepository
from app.platform.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a FinSightAgent user")
    parser.add_argument("username")
    parser.add_argument(
        "--role",
        choices=("researcher", "reviewer", "publisher", "admin"),
        required=True,
    )
    parser.add_argument("--password")
    args = parser.parse_args()
    settings = Settings.from_environment()
    if settings.repository != "postgresql":
        parser.error("FINSIGHT_REPOSITORY must be postgresql for persistent provisioning")
    password = args.password or getpass.getpass("Password: ")
    repository = SqlAlchemyRepository(settings.database_url)
    if repository.get_user_by_username(args.username):
        parser.error("username already exists")
    repository.save_user(
        User(
            id=new_id("usr"),
            username=args.username,
            password_hash=PASSWORD_HASH.hash(password),
            role=args.role,
            created_at=datetime.now(timezone.utc),
        )
    )
    print(f"created user {args.username} ({args.role})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
