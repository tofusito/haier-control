from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from ipaddress import IPv4Address, IPv6Address
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, Response, status

if TYPE_CHECKING:
    from app.settings import Settings


TRUSTED_SESSION_COOKIE = "haier_trusted_session"
TRUSTED_SESSION_VERSION = "v1"
TRUSTED_SESSION_PREFIX = b"haier-control/trusted-session:"
TRUSTED_DIGEST_PREFIX = b"haier-control/trusted-client:"

IPAddress = IPv4Address | IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def configured_networks(settings: Settings) -> tuple[IPNetwork, ...]:
    networks: list[IPNetwork] = []
    for value in settings.trusted_network_cidrs.split(","):
        candidate = value.strip()
        if not candidate:
            continue
        networks.append(ipaddress.ip_network(candidate, strict=False))
    return tuple(networks)


def validate_configuration(settings: Settings) -> None:
    if not settings.trusted_network_mode:
        return
    networks = configured_networks(settings)
    if not networks:
        raise RuntimeError(
            "HAIER_TRUSTED_NETWORK_MODE requires at least one valid "
            "HAIER_TRUSTED_NETWORK_CIDRS network"
        )
    # A default route would silently trust every source that can reach the port,
    # which is the one misconfiguration this mode must never accept quietly.
    for network in networks:
        if network.prefixlen == 0:
            raise RuntimeError(
                f"HAIER_TRUSTED_NETWORK_CIDRS must not contain a default route: {network}"
            )


def _client_address(request: Request) -> IPAddress | None:
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def is_trusted_client(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    if not settings.trusted_network_mode:
        return False
    address = _client_address(request)
    if address is None:
        return False
    return any(address in network for network in configured_networks(settings))


def _signature(master_key: bytes, payload: str) -> str:
    return hmac.new(
        master_key,
        TRUSTED_SESSION_PREFIX + payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _cookie_payload(master_key: bytes, issued_at: int) -> str:
    payload = f"{TRUSTED_SESSION_VERSION}.{issued_at}"
    return f"{payload}.{_signature(master_key, payload)}"


def session_cookie_valid(request: Request) -> bool:
    if not is_trusted_client(request):
        return False
    value = request.cookies.get(TRUSTED_SESSION_COOKIE, "")
    parts = value.split(".")
    if len(parts) != 3 or parts[0] != TRUSTED_SESSION_VERSION:
        return False
    try:
        issued_at = int(parts[1])
    except ValueError:
        return False
    now = int(time.time())
    ttl = request.app.state.settings.trusted_session_ttl_seconds
    if issued_at > now + 60 or now - issued_at > ttl:
        return False
    expected = _signature(request.app.state.master_key, ".".join(parts[:2]))
    return hmac.compare_digest(parts[2], expected)


def issue_session_cookie(request: Request, response: Response) -> None:
    if not is_trusted_client(request) or session_cookie_valid(request):
        return
    response.set_cookie(
        TRUSTED_SESSION_COOKIE,
        _cookie_payload(request.app.state.master_key, int(time.time())),
        max_age=request.app.state.settings.trusted_session_ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )


def trusted_client_digest(request: Request) -> str:
    address = _client_address(request)
    value = str(address or "unknown").encode("ascii")
    return hmac.new(
        request.app.state.master_key,
        TRUSTED_DIGEST_PREFIX + value,
        hashlib.sha256,
    ).hexdigest()


def require_trusted_network(request: Request) -> None:
    if request.app.state.settings.trusted_network_mode and not is_trusted_client(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Trusted home network required")
