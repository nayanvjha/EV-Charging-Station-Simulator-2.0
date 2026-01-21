import argparse
import secrets

from db import create_user, get_user_by_email, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="User admin utility.")
    sub = parser.add_subparsers(dest="command", required=True)

    create_parser = sub.add_parser("create", help="Create a new user")
    create_parser.add_argument("--email", required=True, help="User email")

    args = parser.parse_args()

    if args.command == "create":
        init_db()
        existing = get_user_by_email(args.email)
        if existing:
            print(f"User already exists: {existing['email']} -> API Key: {existing['api_key']}")
            return
        api_key = secrets.token_urlsafe(32)
        user = create_user(args.email, api_key)
        print(f"User: {user['email']} -> API Key: {user['api_key']}")


if __name__ == "__main__":
    main()
