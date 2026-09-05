from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.drivers.base import DriverUnavailable
from app.drivers.haier_auth import HaierTokens
from app.drivers.haier_cloud import CloudDevice, HaierCloudDriver
from app.models import DeviceCapabilities
from app.security import token_hash
from app.setup_flow import SetupFlowManager
from tests.test_setup_flow import manager

OLD = HaierTokens("access", "refresh", "identity", "cognito", "mobile")
NEW = HaierTokens("access2", "refresh2", "identity2", "cognito2", "mobile")


def driver(path: Path) -> HaierCloudDriver:
    return HaierCloudDriver(b"k" * 40, path / "session.enc", "client")


@pytest.mark.asyncio
async def test_restart_keeps_tokens_and_private_recovery_credentials(tmp_path: Path) -> None:
    first = driver(tmp_path)
    first.store_tokens(OLD)
    first.configure_credentials("person@example.invalid", "secret-example")
    await first.close()
    second = driver(tmp_path)
    await second.start()
    assert second._tokens == OLD
    assert second.saved_credentials() == {
        "email": "person@example.invalid",
        "password": "secret-example",
    }
    for path in (tmp_path / "session.enc", tmp_path / "haier-credentials.enc"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert b"secret-example" not in path.read_bytes()
    await second.close()


@pytest.mark.asyncio
async def test_concurrent_expired_requests_refresh_once_and_persist(tmp_path: Path) -> None:
    value = driver(tmp_path)
    value.store_tokens(OLD)
    value._auth.refresh = AsyncMock(return_value=NEW)
    await value._client.aclose()
    value._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200 if request.headers["id-token"] == "identity2" else 401,
                json={"ok": True},
            )
        )
    )
    await asyncio.gather(*(value._request("GET", "/example") for _ in range(5)))
    value._auth.refresh.assert_awaited_once()
    await value.close()
    restored = driver(tmp_path)
    await restored.start()
    assert restored._tokens == NEW
    await restored.close()


@pytest.mark.asyncio
async def test_invalid_refresh_recovers_with_saved_credentials(tmp_path: Path) -> None:
    value = driver(tmp_path)
    value.store_tokens(OLD)
    value.configure_credentials("person@example.invalid", "secret-example")
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid"))
    value._auth.refresh = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "invalid grant",
            request=response.request,
            response=response,
        )
    )
    value._auth.login = AsyncMock(return_value=NEW)
    await value._recover_session(OLD)
    assert value._tokens == NEW
    value._auth.login.assert_awaited_once_with("person@example.invalid", "secret-example")
    await value.close()


@pytest.mark.asyncio
async def test_network_outage_never_repeats_password_login(tmp_path: Path) -> None:
    value = driver(tmp_path)
    value.store_tokens(OLD)
    value.configure_credentials("person@example.invalid", "secret-example")
    value._auth.refresh = AsyncMock(side_effect=httpx.ConnectError("offline"))
    value._auth.login = AsyncMock()
    for _ in range(3):
        with pytest.raises(DriverUnavailable):
            await value._recover_session(OLD)
    value._auth.refresh.assert_awaited_once()
    value._auth.login.assert_not_awaited()
    assert not value.requires_reauth
    await value.close()


@pytest.mark.asyncio
async def test_inventory_survives_restart_and_discovery_outage(tmp_path: Path) -> None:
    value = driver(tmp_path)
    value.store_tokens(OLD)
    value._request = AsyncMock(
        return_value={
            "modules": {
                "applianceList": {
                    "payload": {
                        "appliances": [
                            {"applianceTypeName": "AC", "macAddress": "test-mac", "nickName": "AC"}
                        ],
                    }
                }
            }
        }
    )

    async def schema(device: CloudDevice) -> None:
        device.capabilities = DeviceCapabilities(modes=[])
        device.commands = {"settings": {"example": "schema"}}

    value._ensure_schema = schema  # type: ignore[method-assign]
    original = await value.list_devices()
    await value.list_devices()
    value._request.assert_awaited_once()
    await value.close()
    restored = driver(tmp_path)
    await restored.start()
    restored._request = AsyncMock(side_effect=httpx.ConnectError("offline"))
    assert await restored.list_devices() == original
    assert restored._devices[original[0].id].commands == {"settings": {"example": "schema"}}
    assert b"test-mac" not in (tmp_path / "haier-devices.enc").read_bytes()
    await restored.close()


@pytest.mark.asyncio
async def test_browser_token_survives_reads_restart_until_correct_ack(tmp_path: Path) -> None:
    flow, fake_driver, _ = await manager(tmp_path, "success")
    await flow.begin_automatic("person@example.invalid", "secret-example")
    token = flow.automatic_status().api_token
    assert token
    assert flow.automatic_status().api_token == token
    assert flow.automatic_status(expose_api_token=False).api_token is None
    restarted = SetupFlowManager(
        fake_driver,
        flow.database,
        b"k" * 40,
        "client",  # type: ignore[arg-type]
    )
    assert restarted.automatic_status().api_token == token
    restarted.acknowledge_browser("wrong")
    assert restarted.automatic_status().api_token == token
    restarted.acknowledge_browser(token_hash(token, b"k" * 40))
    assert restarted.automatic_status().api_token is None
    assert not (tmp_path / "browser-setup.enc").exists()
    await flow.close()
    await restarted.close()
