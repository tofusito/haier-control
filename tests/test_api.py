from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest


@pytest.mark.asyncio
async def test_health_is_only_public_operational_route(client: httpx.AsyncClient) -> None:
    health = await client.get("/healthz")
    devices = await client.get("/api/v1/devices")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert devices.status_code == 401
    ack = await client.post("/api/v1/setup/haier/ack")
    assert ack.status_code == 401


@pytest.mark.asyncio
async def test_bootstrap_is_one_use(client: httpx.AsyncClient) -> None:
    headers = {"X-Bootstrap-Token": "bootstrap-secret-that-is-long-enough"}
    payload = {"name": "first", "scopes": ["read"]}
    first = await client.post("/api/v1/setup/tokens", headers=headers, json=payload)
    second = await client.post("/api/v1/setup/tokens", headers=headers, json=payload)
    assert first.status_code == 201
    assert first.json()["token"].startswith("hc_")
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_devices_are_capability_based(
    api_client: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = api_client
    response = await client.get("/api/v1/devices", headers=headers)
    assert response.status_code == 200
    devices = response.json()
    assert [item["name"] for item in devices] == ["Salón", "Dormitorio"]
    assert "cool" in devices[0]["capabilities"]["modes"]
    assert devices[0]["state"]["room_temperature"] is not None


@pytest.mark.asyncio
async def test_command_and_duplicate_suppression(
    api_client: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = api_client
    payload = {"operation": "set_temperature", "value": 22.5}
    first = await client.post("/api/v1/devices/salon/commands", headers=headers, json=payload)
    second = await client.post("/api/v1/devices/salon/commands", headers=headers, json=payload)
    assert first.status_code == 200
    assert first.json()["state"]["target_temperature"] == 22.5
    assert second.status_code == 200
    assert second.json()["accepted"] is False
    assert second.json()["message"] == "Duplicate command suppressed"


@pytest.mark.asyncio
async def test_create_edit_and_cancel_timer(
    api_client: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = api_client
    execute_at = datetime.now(UTC) + timedelta(hours=1)
    created = await client.post(
        "/api/v1/timers",
        headers=headers,
        json={
            "device_id": "dormitorio",
            "action": "on",
            "execute_at": execute_at.isoformat(),
            "command": {"mode": "cool", "temperature": 23, "fan_mode": "low"},
        },
    )
    assert created.status_code == 201
    timer_id = created.json()["id"]
    edited_at = execute_at + timedelta(minutes=30)
    edited = await client.patch(
        f"/api/v1/timers/{timer_id}",
        headers=headers,
        json={"execute_at": edited_at.isoformat()},
    )
    cancelled = await client.delete(f"/api/v1/timers/{timer_id}", headers=headers)
    assert edited.status_code == 200
    assert edited.json()["execute_at"].startswith(edited_at.isoformat()[:19])
    assert cancelled.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_timer_rejects_past_time(
    api_client: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = api_client
    response = await client.post(
        "/api/v1/timers",
        headers=headers,
        json={
            "device_id": "salon",
            "action": "off",
            "execute_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_openapi_requires_read_scope(
    api_client: tuple[httpx.AsyncClient, dict[str, str]],
) -> None:
    client, headers = api_client
    public = await client.get("/api/v1/openapi.json")
    private = await client.get("/api/v1/openapi.json", headers=headers)
    assert public.status_code == 401
    assert private.status_code == 200
    assert "/api/v1/timers" in private.json()["paths"]
