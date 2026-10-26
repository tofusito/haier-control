"""The evaluation runner must never inherit a live cloud deployment."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI
from pytest import MonkeyPatch

from scripts import demo


def test_demo_isolated_from_live_environment(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    production_db = tmp_path / "production.db"
    monkeypatch.setenv("HAIER_DRIVER", "haier-cloud")
    monkeypatch.setenv("HAIER_DATABASE_PATH", str(production_db))
    monkeypatch.setenv("HAIER_BIND_HOST", "0.0.0.0")  # noqa: S104 - test ignores a live bind setting
    monkeypatch.setenv("HAIER_HON_EMAIL", "private@example.invalid")
    monkeypatch.setattr("sys.argv", ["demo", "--port", "8789"])

    async def check(app: FastAPI) -> None:
        async with app.router.lifespan_context(app):
            config = app.state.settings
            assert config.driver == "mock"
            assert config.database_path != production_db
            assert config.hon_email is None
            assert config.trusted_network_cidrs == "127.0.0.1/32,::1/128"
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
                await client.get("/")
                assert (await client.get("/healthz")).json()["driver"] == "mock"
                devices = (await client.get("/api/v1/devices")).json()
                assert [device["name"] for device in devices] == ["Living room", "Bedroom"]

    def fake_run(app: FastAPI, *, host: str, port: int, proxy_headers: bool) -> None:
        assert host == "127.0.0.1"
        assert port == 8789
        assert proxy_headers is False
        asyncio.run(check(app))

    monkeypatch.setattr("scripts.demo.uvicorn.run", fake_run)
    demo.main()
    assert not production_db.exists()
