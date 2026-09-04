from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.drivers.base import DriverUnavailable, UnsupportedCapability
from app.drivers.haier_auth import API_URL, HaierAuthenticator, HaierTokens, device_payload
from app.models import (
    AdvancedCapability,
    CommandRequest,
    CommandResult,
    DeviceCapabilities,
    DeviceMode,
    DeviceState,
    DeviceSummary,
)
from app.security import SecretBox

_LOGGER = logging.getLogger(__name__)

MODE_FROM_RAW = {
    "0": DeviceMode.AUTO,
    "1": DeviceMode.COOL,
    "2": DeviceMode.DRY,
    "4": DeviceMode.HEAT,
    "6": DeviceMode.FAN,
}
RAW_FROM_MODE = {value.value: key for key, value in MODE_FROM_RAW.items()}
FAN_FROM_RAW = {"5": "auto", "3": "low", "2": "medium", "1": "high"}
RAW_FROM_FAN = {value: key for key, value in FAN_FROM_RAW.items()}
ADVANCED = {
    "eco": ("echoStatus", "Eco"),
    "sleep": ("silentSleepStatus", "Sueño"),
    "rapid": ("rapidMode", "Turbo"),
    "health": ("healthMode", "Health"),
    "display": ("screenDisplayStatus", "Display"),
    "mute": ("muteStatus", "Silencio"),
    "fresh_air": ("freshAirStatus", "Aire fresco"),
    "child_lock": ("lockStatus", "Bloqueo infantil"),
    "light": ("lightStatus", "Luz"),
    "presence": ("humanSensingStatus", "Presencia"),
    "self_clean": ("selfCleaningStatus", "Autolimpieza"),
    "self_clean_56": ("selfCleaning56Status", "Autolimpieza 56 °C"),
    "ten_degree_heat": ("10degreeHeatingStatus", "Calefacción a 10 °C"),
    "electric_heat": ("electricHeatingStatus", "Calefacción eléctrica"),
    "half_degree": ("halfDegreeSettingStatus", "Medio grado"),
}


@dataclass
class CloudDevice:
    public_id: str
    name: str
    model: str | None
    mac: str = field(repr=False)
    appliance_type: str
    model_id: str
    code: str
    raw: dict[str, Any] = field(repr=False)
    commands: dict[str, Any] = field(default_factory=dict, repr=False)
    capabilities: DeviceCapabilities | None = None


def _value(node: Any) -> Any:
    if isinstance(node, dict):
        for key in ("value", "parNewVal", "defaultValue", "fixedValue"):
            if key in node:
                return node[key]
    return node


def _enum_values(schema: dict[str, Any] | None) -> list[str]:
    if not schema:
        return []
    raw = schema.get("enumValues", [])
    if isinstance(raw, str):
        return [part for part in raw.split("|") if part]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HaierCloudDriver:
    """Direct adapter for the private hOn API.

    MQTT is deliberately not enabled in v0.1. REST polling is the authoritative
    reconciliation path and every cloud/schema failure remains visible to callers.
    """

    name = "haier-cloud"

    def __init__(
        self,
        master_key: bytes,
        session_path: Path,
        client_id: str,
        *,
        timeout: float = 15.0,
    ) -> None:
        self._key = master_key
        self._session_path = session_path
        self._box = SecretBox(master_key)
        self._auth = HaierAuthenticator(client_id)
        self._tokens: HaierTokens | None = None
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"user-agent": "haier-control/0.1"}
        )
        self._devices: dict[str, CloudDevice] = {}
        self.last_error: str | None = None

    @property
    def requires_reauth(self) -> bool:
        return self._tokens is None or bool(
            self.last_error and "reauthentication" in self.last_error
        )

    async def start(self) -> None:
        if not self._session_path.exists():
            self.last_error = "Haier authentication has not been bootstrapped"
            return
        try:
            data = self._box.decrypt_json(self._session_path.read_bytes())
            self._tokens = HaierTokens(**data)
        except (OSError, TypeError, ValueError) as exc:
            self.last_error = f"Encrypted Haier session cannot be loaded: {type(exc).__name__}"

    async def close(self) -> None:
        await self._client.aclose()

    async def bootstrap(self, email: str, password: str) -> None:
        tokens = await self._auth.login(email, password)
        self.store_tokens(tokens)

    def store_tokens(self, tokens: HaierTokens) -> None:
        self._persist_tokens(tokens)
        self._tokens = tokens
        self.last_error = None

    def _persist_tokens(self, tokens: HaierTokens) -> None:
        self._session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self._session_path.with_suffix(".tmp")
        temporary.write_bytes(self._box.encrypt_json(tokens.as_dict()))
        temporary.chmod(0o600)
        temporary.replace(self._session_path)

    def _headers(self) -> dict[str, str]:
        if not self._tokens:
            raise DriverUnavailable(self.last_error or "Haier authentication is unavailable")
        return {
            "content-type": "application/json",
            "cognito-token": self._tokens.cognito_token,
            "id-token": self._tokens.id_token,
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._client.request(
            method, f"{API_URL}{path}", headers=self._headers(), **kwargs
        )
        if response.status_code in {401, 403} and self._tokens:
            try:
                self._tokens = await self._auth.refresh(
                    self._tokens.refresh_token, self._tokens.mobile_id
                )
                self._persist_tokens(self._tokens)
            except Exception as exc:  # the public error is fixed; vendor data is never logged
                self.last_error = f"Haier reauthentication required ({type(exc).__name__})"
                raise DriverUnavailable(self.last_error) from exc
            response = await self._client.request(
                method, f"{API_URL}{path}", headers=self._headers(), **kwargs
            )
        if response.status_code == 429:
            raise DriverUnavailable("Haier cloud rate limit reached; retry later")
        if response.status_code >= 500:
            raise DriverUnavailable("Haier cloud is temporarily unavailable")
        if response.status_code >= 400:
            raise DriverUnavailable(f"Haier cloud rejected the request ({response.status_code})")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise DriverUnavailable("Haier cloud returned a non-JSON response") from exc
        if not isinstance(data, dict):
            raise DriverUnavailable("Haier cloud response schema changed")
        self.last_error = None
        return data

    async def list_devices(self) -> list[DeviceSummary]:
        tokens = self._tokens
        if not tokens:
            raise DriverUnavailable(self.last_error or "Haier authentication is unavailable")
        data = await self._request(
            "POST",
            "/unified-api/v1/view/appliance-list",
            json={"deviceId": tokens.mobile_id},
        )
        try:
            appliances = data["modules"]["applianceList"]["payload"]["appliances"]
        except (KeyError, TypeError) as exc:
            raise DriverUnavailable("Haier appliance-list response schema changed") from exc
        if not isinstance(appliances, list):
            raise DriverUnavailable("Haier appliance-list did not contain a list")
        _LOGGER.info(
            "Haier appliance-list appliance_count=%d types=%s has_mac=%s",
            len(appliances),
            sorted(
                {
                    str(item.get("applianceTypeName", "<missing>"))
                    for item in appliances
                    if isinstance(item, dict)
                }
            ),
            [bool(item.get("macAddress")) for item in appliances if isinstance(item, dict)],
        )
        found: dict[str, CloudDevice] = {}
        for item in appliances:
            # The appliance-list response names this field "applianceTypeName" (the
            # outgoing command/context calls below use the differently-named
            # "applianceType" key instead -- an asymmetric hOn API naming quirk).
            if (
                not isinstance(item, dict)
                or str(item.get("applianceTypeName", "")).upper() != "AC"
            ):
                continue
            mac = str(item.get("macAddress", ""))
            if not mac:
                continue
            public_id = hmac.new(self._key, mac.encode(), hashlib.sha256).hexdigest()[:16]
            device = CloudDevice(
                public_id=public_id,
                name=str(item.get("nickName") or item.get("modelName") or "Aire acondicionado"),
                model=str(item.get("modelName") or item.get("applianceModelId") or "") or None,
                mac=mac,
                appliance_type=str(item.get("applianceTypeName", "AC")),
                model_id=str(item.get("applianceModelId", "")),
                code=str(item.get("code", "")),
                raw=item,
            )
            await self._ensure_schema(device)
            found[public_id] = device
        self._devices = found
        return [
            DeviceSummary(
                id=device.public_id,
                name=device.name,
                model=device.model,
                capabilities=device.capabilities or DeviceCapabilities(modes=[]),
            )
            for device in found.values()
        ]

    async def _ensure_schema(self, device: CloudDevice) -> None:
        params: dict[str, Any] = {
            "applianceType": device.appliance_type,
            "applianceModelId": device.model_id,
            "macAddress": device.mac,
            "os": "android",
            "appVersion": "2.27.9",
            "code": device.code,
        }
        extra_fields = (
            ("eepromId", "firmwareId"),
            ("fwVersion", "fwVersion"),
            ("series", "series"),
        )
        for source, target in extra_fields:
            if value := device.raw.get(source):
                params[target] = value
        data = await self._request("GET", "/commands/v1/retrieve", params=params)
        payload = data.get("payload")
        if not isinstance(payload, dict) or payload.get("resultCode") != "0":
            raise DriverUnavailable("Haier command schema is unavailable")
        device.commands = {key: value for key, value in payload.items() if key != "resultCode"}
        settings = self._settings_parameters(device)
        temp_value = settings.get("tempSel")
        temp: dict[str, Any] = temp_value if isinstance(temp_value, dict) else {}
        mode_raw = _enum_values(settings.get("machMode"))
        fan_raw = _enum_values(settings.get("windSpeed"))
        vertical_raw = _enum_values(settings.get("windDirectionVertical"))
        horizontal_raw = _enum_values(settings.get("windDirectionHorizontal"))
        advanced: list[AdvancedCapability] = []
        for key, (raw_name, label) in ADVANCED.items():
            schema = settings.get(raw_name)
            if isinstance(schema, dict):
                options = _enum_values(schema)
                advanced.append(
                    AdvancedCapability(
                        key=key,
                        label=label,
                        kind="toggle" if set(options).issubset({"0", "1"}) else "enum",
                        options=options,
                    )
                )
        device.capabilities = DeviceCapabilities(
            modes=[MODE_FROM_RAW[value] for value in mode_raw if value in MODE_FROM_RAW],
            temperature_min=_number(temp.get("minimumValue")),
            temperature_max=_number(temp.get("maximumValue")),
            temperature_step=_number(temp.get("incrementValue")),
            fan_modes=[FAN_FROM_RAW[value] for value in fan_raw if value in FAN_FROM_RAW],
            vertical_swing=[
                "swing" if value == "8" else f"position_{value}"
                for value in vertical_raw
            ],
            horizontal_swing=[
                "swing" if value == "7" else f"position_{value}"
                for value in horizontal_raw
            ],
            advanced=advanced,
        )

    @staticmethod
    def _settings_parameters(device: CloudDevice) -> dict[str, Any]:
        command = device.commands.get("settings")
        if not isinstance(command, dict):
            return {}
        params = command.get("parameters")
        return params if isinstance(params, dict) else {}

    async def get_state(self, device_id: str) -> DeviceState:
        device = await self._device(device_id)
        data = await self._request(
            "GET",
            "/commands/v1/context",
            params={
                "macAddress": device.mac,
                "applianceType": device.appliance_type,
                "category": "CYCLE",
            },
        )
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise DriverUnavailable("Haier state response schema changed")
        shadow = payload.get("shadow", {})
        shadow_params = shadow.get("parameters", {}) if isinstance(shadow, dict) else {}
        if not isinstance(shadow_params, dict):
            shadow_params = {}
        settings = {
            key: _value(value) for key, value in shadow_params.items() if isinstance(key, str)
        }
        direct = {key: _value(value) for key, value in payload.items() if key != "shadow"}
        event = payload.get("lastConnEvent")
        online = not (isinstance(event, dict) and event.get("category") == "DISCONNECTED")
        mode = MODE_FROM_RAW.get(str(settings.get("machMode")))
        advanced = {
            key: str(settings.get(raw_name)) == "1"
            for key, (raw_name, _label) in ADVANCED.items()
            if raw_name in settings
        }
        vertical = settings.get("windDirectionVertical")
        horizontal = settings.get("windDirectionHorizontal")
        return DeviceState(
            device_id=device_id,
            online=online,
            power=str(settings.get("onOffStatus")) == "1" if "onOffStatus" in settings else None,
            mode=mode,
            target_temperature=_number(settings.get("tempSel")),
            room_temperature=_number(direct.get("tempIndoor")),
            fan_mode=FAN_FROM_RAW.get(str(settings.get("windSpeed"))),
            vertical_swing=(
                "swing" if str(vertical) == "8" else f"position_{vertical}"
            )
            if vertical is not None
            else None,
            horizontal_swing=(
                "swing" if str(horizontal) == "7" else f"position_{horizontal}"
            )
            if horizontal is not None
            else None,
            advanced=advanced,
            updated_at=datetime.now(UTC),
        )

    async def send_command(self, device_id: str, command: CommandRequest) -> CommandResult:
        device = await self._device(device_id)
        settings = self._settings_parameters(device)
        raw_name, raw_value = self._raw_command(device, command)
        schema = settings.get(raw_name)
        if not isinstance(schema, dict):
            raise UnsupportedCapability("The device did not advertise this control")
        parameters: dict[str, Any] = {}
        ancillary: dict[str, Any] = {}
        command_def = device.commands.get("settings", {})
        groups = (("parameters", parameters), ("ancillaryParameters", ancillary))
        for group_name, destination in groups:
            group = command_def.get(group_name, {}) if isinstance(command_def, dict) else {}
            if not isinstance(group, dict):
                continue
            for name, item in group.items():
                if not isinstance(item, dict):
                    continue
                if item.get("mandatory") or name == raw_name:
                    chosen = raw_value if name == raw_name else _value(item)
                    if chosen not in (None, ""):
                        destination[name] = str(chosen)
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        tokens = self._tokens
        assert tokens is not None
        envelope = {
            "macAddress": device.mac,
            "timestamp": timestamp,
            "commandName": "settings",
            "transactionId": f"{device.mac}_{timestamp}",
            "applianceOptions": device.raw.get("applianceOptions", {}),
            "device": device_payload(tokens.mobile_id, mobile=True),
            "attributes": {
                "channel": "mobileApp",
                "origin": "standardProgram",
                "energyLabel": "0",
            },
            "ancillaryParameters": ancillary,
            "parameters": parameters,
            "applianceType": device.appliance_type,
        }
        result = await self._request("POST", "/commands/v1/send", json=envelope)
        payload = result.get("payload")
        accepted = isinstance(payload, dict) and payload.get("resultCode") == "0"
        if not accepted:
            raise DriverUnavailable("Haier cloud did not confirm the command")
        state: DeviceState | None
        try:
            state = await self.get_state(device_id)
            message = "Cloud accepted the command; state reconciled by polling"
        except DriverUnavailable:
            state = None
            message = "Cloud accepted the command; state confirmation is still pending"
        return CommandResult(
            accepted=True,
            device_id=device_id,
            operation=command.operation,
            state=state,
            message=message,
        )

    def _raw_command(self, device: CloudDevice, command: CommandRequest) -> tuple[str, str]:
        caps = device.capabilities or DeviceCapabilities(modes=[])
        match command.operation:
            case "power":
                return "onOffStatus", "1" if bool(command.value) else "0"
            case "set_mode":
                value = str(command.value)
                if DeviceMode(value) not in caps.modes:
                    raise UnsupportedCapability("Mode is not supported")
                return "machMode", RAW_FROM_MODE[value]
            case "set_temperature":
                numeric_value = float(command.value)
                if caps.temperature_min is None or caps.temperature_max is None:
                    raise UnsupportedCapability("Temperature control is unavailable")
                if not caps.temperature_min <= numeric_value <= caps.temperature_max:
                    raise UnsupportedCapability("Temperature is outside the device range")
                return "tempSel", f"{numeric_value:g}"
            case "set_fan":
                value = str(command.value)
                if value not in caps.fan_modes:
                    raise UnsupportedCapability("Fan mode is not supported")
                return "windSpeed", RAW_FROM_FAN[value]
            case "set_vertical_swing":
                value = str(command.value)
                if value not in caps.vertical_swing:
                    raise UnsupportedCapability("Vertical swing is not supported")
                raw = "8" if value == "swing" else value.removeprefix("position_")
                return "windDirectionVertical", raw
            case "set_horizontal_swing":
                value = str(command.value)
                if value not in caps.horizontal_swing:
                    raise UnsupportedCapability("Horizontal swing is not supported")
                raw = "7" if value == "swing" else value.removeprefix("position_")
                return "windDirectionHorizontal", raw
            case "set_advanced":
                if command.key not in ADVANCED:
                    raise UnsupportedCapability("Advanced capability is not supported")
                raw = ADVANCED[command.key][0]
                if raw not in self._settings_parameters(device):
                    raise UnsupportedCapability("Advanced capability was not advertised")
                return raw, "1" if bool(command.value) else "0"
        raise UnsupportedCapability("Unknown command")

    async def _device(self, device_id: str) -> CloudDevice:
        if device_id not in self._devices:
            await self.list_devices()
        try:
            return self._devices[device_id]
        except KeyError:
            raise UnsupportedCapability("Unknown device") from None
