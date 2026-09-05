"""Contract tests against recorded, redacted hOn cloud responses.

Every field name and nesting level this driver reads is an undocumented detail of
a private API. Four shipped bugs came from guessing them, each invisible until the
dashboard was empty in a browser. These tests parse real recorded payloads with
the real driver code, so a moved field fails here instead of in the living room.

See tests/fixtures/README.md for how the recordings were captured and redacted.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.drivers.haier_auth import HaierTokens
from app.drivers.haier_cloud import HaierCloudDriver
from app.models import DeviceMode

FIXTURES = Path(__file__).parent / "fixtures"
TOKENS = HaierTokens("access", "refresh", "identity", "cognito", "mobile-1")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _recorded_cloud() -> httpx.MockTransport:
    appliance_list = _fixture("appliance_list.json")
    command_schema = _fixture("command_schema_ac.json")
    context = _fixture("context_ac.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/unified-api/v1/view/appliance-list":
            return httpx.Response(200, json=appliance_list)
        if request.url.path == "/commands/v1/retrieve":
            return httpx.Response(200, json=command_schema)
        if request.url.path == "/commands/v1/context":
            return httpx.Response(200, json=context)
        raise AssertionError(f"unexpected path {request.url.path}")

    return httpx.MockTransport(handler)


@pytest.fixture
def driver(tmp_path: Path) -> HaierCloudDriver:
    value = HaierCloudDriver(b"k" * 40, tmp_path / "session.enc", "client")
    value._tokens = TOKENS
    value._client = httpx.AsyncClient(transport=_recorded_cloud())
    return value


@pytest.mark.asyncio
async def test_recorded_appliance_list_yields_both_air_conditioners(
    driver: HaierCloudDriver,
) -> None:
    try:
        devices = await driver.list_devices()
    finally:
        await driver.close()

    assert len(devices) == 2
    assert all(device.model for device in devices)
    # Public ids are derived from the MAC, so they must be stable and distinct.
    assert len({device.id for device in devices}) == 2


@pytest.mark.asyncio
async def test_recorded_schema_advertises_the_controls_the_ui_renders(
    driver: HaierCloudDriver,
) -> None:
    try:
        devices = await driver.list_devices()
    finally:
        await driver.close()

    capabilities = devices[0].capabilities
    assert DeviceMode.COOL in capabilities.modes
    assert DeviceMode.HEAT in capabilities.modes
    assert capabilities.fan_modes, "fan speeds must survive the schema walk"
    assert capabilities.temperature_min is not None
    assert capabilities.temperature_max is not None
    assert capabilities.temperature_min < capabilities.temperature_max
    assert capabilities.vertical_swing, "vertical swing positions must be advertised"
    assert {item.key for item in capabilities.advanced} >= {"eco", "sleep"}


@pytest.mark.asyncio
async def test_recorded_context_fills_every_state_field_the_cards_show(
    driver: HaierCloudDriver,
) -> None:
    try:
        devices = await driver.list_devices()
        state = await driver.get_state(devices[0].id)
    finally:
        await driver.close()

    assert state.online is True
    assert state.power is not None
    assert state.mode is not None
    assert state.target_temperature is not None
    # The bug that showed "Sin dato de ambiente": tempIndoor lives in the shadow.
    assert state.room_temperature is not None
    assert state.fan_mode is not None
    assert state.advanced, "advanced toggles must be reported, not silently empty"
