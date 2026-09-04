from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.drivers.haier_auth import HaierTokens
from app.drivers.haier_cloud import HaierCloudDriver

TOKENS = HaierTokens("access", "refresh", "identity", "cognito", "mobile-1")


def _driver(tmp_path: Path, handler: httpx.MockTransport) -> HaierCloudDriver:
    driver = HaierCloudDriver(b"k" * 40, tmp_path / "session.enc", "client")
    driver._tokens = TOKENS  # bypass start(): no persisted session in this test
    driver._client = httpx.AsyncClient(transport=handler)
    return driver


@pytest.mark.asyncio
async def test_list_devices_reads_the_type_field_the_cloud_actually_sends(
    tmp_path: Path,
) -> None:
    """Reproduces the real symptom: the hOn account had two working AC units (seen
    live in the official app and in Home Assistant/addhOn), but /api/v1/devices
    returned an empty list. The appliance-list response names the type field
    "applianceTypeName", not "applianceType" (that name is only used on OUTGOING
    command/context requests) -- the old filter read the wrong key and silently
    dropped every appliance.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/unified-api/v1/view/appliance-list":
            return httpx.Response(
                200,
                json={
                    "modules": {
                        "applianceList": {
                            "payload": {
                                "appliances": [
                                    {
                                        "applianceTypeName": "AC",
                                        "macAddress": "AA:BB:CC:DD:EE:FF",
                                        "nickName": "Salón",
                                        "applianceModelId": "123",
                                    },
                                    {
                                        "applianceTypeName": "WM",
                                        "macAddress": "11:22:33:44:55:66",
                                        "nickName": "Lavadora",
                                    },
                                ]
                            }
                        }
                    }
                },
            )
        assert request.url.path == "/commands/v1/retrieve"
        return httpx.Response(200, json={"payload": {"resultCode": "0"}})

    driver = _driver(tmp_path, httpx.MockTransport(handler))
    try:
        devices = await driver.list_devices()
    finally:
        await driver.close()

    assert [device.name for device in devices] == ["Salón"]


@pytest.mark.asyncio
async def test_list_devices_skips_appliances_without_a_mac(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "modules": {
                    "applianceList": {
                        "payload": {"appliances": [{"applianceTypeName": "AC"}]}
                    }
                }
            },
        )

    driver = _driver(tmp_path, httpx.MockTransport(handler))
    try:
        devices = await driver.list_devices()
    finally:
        await driver.close()

    assert devices == []
