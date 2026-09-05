from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.main import create_app
from app.settings import Settings
from app.trusted_access import TRUSTED_SESSION_COOKIE


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
async def test_trusted_network_browser_session_needs_no_bearer_token(
    settings: Settings, master_key: bytes
) -> None:
    trusted_settings = settings.model_copy(
        update={
            "trusted_network_mode": True,
            "trusted_network_cidrs": "192.0.2.0/24,100.64.0.0/10",
        }
    )
    app = create_app(
        trusted_settings,
        master_key=master_key,
        bootstrap_secret=b"bootstrap-secret-that-is-long-enough",
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("192.0.2.42", 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://trusted") as value:
            root = await value.get("/")
            health = await value.get("/healthz")
            devices = await value.get("/api/v1/devices")
    assert root.status_code == 200
    assert "haier_trusted_session=" in root.headers["set-cookie"]
    assert health.json()["trusted_network"] is True
    assert devices.status_code == 200
    assert devices.json()[0]["name"] == "Salón"


@pytest.mark.asyncio
async def test_trusted_network_accepts_tailscale_range_but_rejects_other_sources(
    settings: Settings, master_key: bytes
) -> None:
    trusted_settings = settings.model_copy(
        update={
            "trusted_network_mode": True,
            "trusted_network_cidrs": "192.0.2.0/24,100.64.0.0/10",
        }
    )
    app = create_app(
        trusted_settings,
        master_key=master_key,
        bootstrap_secret=b"bootstrap-secret-that-is-long-enough",
    )
    async with app.router.lifespan_context(app):
        tailnet_transport = httpx.ASGITransport(app=app, client=("100.64.0.1", 1234))
        async with httpx.AsyncClient(
            transport=tailnet_transport, base_url="http://tailnet"
        ) as tailnet:
            tailnet_root = await tailnet.get("/")
            tailnet_devices = await tailnet.get("/api/v1/devices")
        external_transport = httpx.ASGITransport(app=app, client=("203.0.113.20", 1234))
        async with httpx.AsyncClient(
            transport=external_transport, base_url="http://external"
        ) as external:
            external_root = await external.get("/")
            external_devices = await external.get("/api/v1/devices")
    assert tailnet_root.status_code == 200
    assert tailnet_devices.status_code == 200
    assert external_root.status_code == 403
    assert external_devices.status_code == 401


@pytest.mark.asyncio
async def test_trusted_mode_requires_explicit_network_configuration(
    settings: Settings, master_key: bytes
) -> None:
    trusted_settings = settings.model_copy(update={"trusted_network_mode": True})
    app = create_app(trusted_settings, master_key=master_key)
    with pytest.raises(RuntimeError, match="TRUSTED_NETWORK_CIDRS"):
        async with app.router.lifespan_context(app):
            pass


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


@pytest.mark.asyncio
async def test_trusted_network_still_requires_the_signed_session_cookie(
    settings: Settings, master_key: bytes
) -> None:
    """The trust boundary is two gates, not one.

    A trusted source address alone must never authorize the API: without that
    second gate any page the household browses could drive the air conditioners
    from the LAN via a cross-site request. Covers a missing cookie, a forged
    one, and a genuine cookie replayed from outside the trusted networks.
    """
    trusted_settings = settings.model_copy(
        update={
            "trusted_network_mode": True,
            "trusted_network_cidrs": "192.0.2.0/24,100.64.0.0/10",
        }
    )
    app = create_app(
        trusted_settings,
        master_key=master_key,
        bootstrap_secret=b"bootstrap-secret-that-is-long-enough",
    )
    inside = httpx.ASGITransport(app=app, client=("192.0.2.42", 1234))
    outside = httpx.ASGITransport(app=app, client=("203.0.113.20", 1234))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=inside, base_url="http://trusted") as client:
            no_cookie = await client.get("/api/v1/devices")
            command = await client.post(
                "/api/v1/devices/salon/commands",
                json={"operation": "power", "power": True},
            )
        async with httpx.AsyncClient(transport=inside, base_url="http://trusted") as client:
            client.cookies.set(TRUSTED_SESSION_COOKIE, "v1.99999999999.not-a-real-signature")
            forged = await client.get("/api/v1/devices")
        async with httpx.AsyncClient(transport=inside, base_url="http://trusted") as client:
            await client.get("/")
            stolen = client.cookies.get(TRUSTED_SESSION_COOKIE)
        async with httpx.AsyncClient(transport=outside, base_url="http://external") as client:
            client.cookies.set(TRUSTED_SESSION_COOKIE, str(stolen))
            replayed = await client.get("/api/v1/devices")

    assert no_cookie.status_code == 401
    assert command.status_code == 401
    assert forged.status_code == 401
    assert replayed.status_code == 401


@pytest.mark.asyncio
async def test_trusted_mode_rejects_a_default_route(
    settings: Settings, master_key: bytes
) -> None:
    """`0.0.0.0/0` would trust every source that can reach the port."""
    trusted_settings = settings.model_copy(
        update={"trusted_network_mode": True, "trusted_network_cidrs": "192.0.2.0/24,0.0.0.0/0"}
    )
    app = create_app(trusted_settings, master_key=master_key)
    with pytest.raises(RuntimeError, match="default route"):
        async with app.router.lifespan_context(app):
            pass
