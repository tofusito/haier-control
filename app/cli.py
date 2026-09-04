from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

import uvicorn

from app.database import Database
from app.drivers.haier_auth import InteractiveHaierLogin
from app.drivers.haier_cloud import HaierCloudDriver
from app.security import new_api_token, token_hash
from app.settings import Settings, load_secret


async def _create_token(settings: Settings, name: str, scopes: set[str]) -> None:
    key = load_secret(settings.master_key_file)
    database = Database(settings.database_path)
    await database.initialize()
    token = new_api_token()
    await database.create_token(name, token_hash(token, key), scopes)
    print(token)
    print("This token will not be shown again.", file=sys.stderr)


async def _haier_login(settings: Settings, email: str | None) -> None:
    key = load_secret(settings.master_key_file)
    account = email or (await asyncio.to_thread(input, "hOn email: ")).strip()
    password = await asyncio.to_thread(getpass.getpass, "hOn password (never stored): ")
    driver = HaierCloudDriver(
        key, settings.encrypted_session_file, settings.haier_client_id
    )
    session = InteractiveHaierLogin(settings.haier_client_id)
    try:
        tokens = await session.begin(account, password)
        password = ""
        if tokens is None:
            code = (
                await asyncio.to_thread(getpass.getpass, "Email verification code: ")
            ).strip()
            tokens = await session.submit_code(code)
            code = ""
        driver.store_tokens(tokens)
    finally:
        password = ""  # noqa: F841 - make the intent explicit before leaving the process
        await session.close()
        await driver.close()
    print("Encrypted hOn session stored; password was not persisted.")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="haier-control")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run the web application")
    token = commands.add_parser("token", help="Create a local API token")
    token.add_argument("--name", default="local-admin")
    token.add_argument(
        "--scopes", default="read,control,timers", help="Comma-separated scopes"
    )
    auth = commands.add_parser("auth", help="Bootstrap the encrypted hOn session")
    auth.add_argument("--email", help="hOn account email (otherwise prompted)")
    return root


def main() -> None:
    args = parser().parse_args()
    settings = Settings()
    if args.command == "serve":
        uvicorn.run(
            "app.main:app",
            host=settings.bind_host,
            port=settings.port,
            log_config=None,
            proxy_headers=False,
        )
    elif args.command == "token":
        scopes = {value.strip() for value in args.scopes.split(",") if value.strip()}
        allowed = {"read", "control", "timers"}
        if not scopes or not scopes.issubset(allowed):
            raise SystemExit("Scopes must be a non-empty subset of read,control,timers")
        asyncio.run(_create_token(settings, args.name, scopes))
    elif args.command == "auth":
        asyncio.run(_haier_login(settings, args.email))


if __name__ == "__main__":
    main()
