from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.drivers.base import DriverUnavailable
from app.drivers.haier_auth import HaierTokens
from app.drivers.haier_cloud import CloudDevice, HaierCloudDriver
from app.models import CommandRequest, DeviceCapabilities, DeviceMode


def device() -> CloudDevice:
    return CloudDevice(
        "ac",
        "AC",
        None,
        "test-mac",
        "AC",
        "model",
        "code",
        {},
        commands={
            "settings": {
                "setParameters": {
                    "parameters": {
                        "onOffStatus": {"fixedValue": "1", "mandatory": 1},
                        "operationName": {"fixedValue": "grSetDAC", "mandatory": 1},
                        "machMode": {"defaultValue": "0", "mandatory": 1},
                        "tempSel": {"defaultValue": "22", "mandatory": 1},
                    },
                    "ancillaryParameters": {
                        "programRules": {
                            "fixedValue": {"rule": {"fixedValue": "0"}},
                            "mandatory": 1,
                        },
                        "optional": {"fixedValue": "1", "mandatory": 0},
                    },
                }
            }
        },
        capabilities=DeviceCapabilities(
            modes=[DeviceMode.COOL], temperature_min=16, temperature_max=30
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("power", [True, False])
async def test_power_payload_includes_nested_parameters_and_preserves_settings(
    tmp_path: Path,
    power: bool,
) -> None:
    driver = HaierCloudDriver(b"k" * 40, tmp_path / "session.enc", "client")
    driver._tokens = HaierTokens("a", "r", "i", "c", "mobile")
    driver._devices = {"ac": device()}
    current = {"onOffStatus": "0", "machMode": "1", "tempSel": "27"}
    context = {"payload": {"shadow": {"parameters": current}}}
    driver._request = AsyncMock(side_effect=[context, {"payload": {"resultCode": "0"}}, context])
    try:
        result = await driver.send_command("ac", CommandRequest(operation="power", value=power))
        assert result.accepted
        call = driver._request.call_args_list[1]
        assert call.args == ("POST", "/commands/v1/send")
        envelope = call.kwargs["json"]
        assert envelope["parameters"] == {
            "onOffStatus": "1" if power else "0",
            "operationName": "grSetDAC",
            "machMode": "1",
            "tempSel": "27",
        }
        assert envelope["ancillaryParameters"] == {"programRules": {"rule": {"fixedValue": "0"}}}
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_temperature_change_does_not_power_on_an_off_unit(tmp_path: Path) -> None:
    driver = HaierCloudDriver(b"k" * 40, tmp_path / "session.enc", "client")
    try:
        params, _ = driver._settings_values(
            device(),
            CommandRequest(operation="set_temperature", value=26),
            {"onOffStatus": "0", "machMode": "1", "tempSel": "27"},
        )
        assert params["tempSel"] == "26"
        assert params["onOffStatus"] == "0"
        assert params["machMode"] == "1"
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_missing_current_settings_prevents_dispatch(tmp_path: Path) -> None:
    driver = HaierCloudDriver(b"k" * 40, tmp_path / "session.enc", "client")
    driver._devices = {"ac": device()}
    driver._request = AsyncMock(return_value={"payload": {"shadow": {"parameters": {}}}})
    try:
        with pytest.raises(DriverUnavailable, match="incomplete"):
            await driver.send_command("ac", CommandRequest(operation="power", value=True))
        driver._request.assert_awaited_once()
        assert driver._request.call_args.args[0] == "GET"
    finally:
        await driver.close()
