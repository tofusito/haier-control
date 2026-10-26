"""Run an isolated, loopback-only mock demo: python -m scripts.demo."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

from app.drivers.mock import MockDriver
from app.main import create_app
from app.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="haier-control-demo-") as directory:
        runtime = Path(directory)
        settings = Settings(
            driver="mock",
            database_path=runtime / "demo.db",
            encrypted_session_file=runtime / "unused-session.enc",
            trusted_network_config_file=runtime / "unused-network.conf",
            trusted_network_mode=True,
            trusted_network_cidrs="127.0.0.1/32,::1/128",
            bind_host="127.0.0.1",
            port=args.port,
            hon_email=None,
            hon_password=None,
            hon_email_file=None,
            hon_password_file=None,
        )
        app = create_app(
            settings,
            master_key=secrets.token_bytes(32),
            bootstrap_secret=b"",
            driver=MockDriver(),
        )
        print(f"Mock demo: http://127.0.0.1:{args.port}")
        print("Simulated devices only. Demo state resets when the process stops.")
        uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
