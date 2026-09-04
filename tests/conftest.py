from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def master_key() -> bytes:
    return b"test-master-key-material-that-is-long-enough"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        driver="mock",
        database_path=tmp_path / "test.db",
        master_key_file=tmp_path / "missing-master",
        bootstrap_token_file=tmp_path / "missing-bootstrap",
        encrypted_session_file=tmp_path / "session.enc",
        command_dedupe_seconds=1,
    )


@pytest.fixture
async def client(settings: Settings, master_key: bytes) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        settings,
        master_key=master_key,
        bootstrap_secret=b"bootstrap-secret-that-is-long-enough",
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
            yield value


@pytest.fixture
async def api_client(client: httpx.AsyncClient) -> tuple[httpx.AsyncClient, dict[str, str]]:
    response = await client.post(
        "/api/v1/setup/tokens",
        headers={"X-Bootstrap-Token": "bootstrap-secret-that-is-long-enough"},
        json={"name": "test", "scopes": ["read", "control", "timers"]},
    )
    assert response.status_code == 201
    token = response.json()["token"]
    return client, {"Authorization": f"Bearer {token}"}
