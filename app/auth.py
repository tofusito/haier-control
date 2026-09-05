from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.database import Database
from app.rate_limit import RateLimiter
from app.security import token_hash
from app.trusted_access import is_trusted_client, session_cookie_valid, trusted_client_digest

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    digest: str
    scopes: set[str]
    trusted_network: bool = False


async def principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials:
        if credentials.scheme.lower() != "bearer":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
        master_key: bytes = request.app.state.master_key
        database: Database = request.app.state.database
        digest = token_hash(credentials.credentials, master_key)
        scopes = await database.authenticate_token(digest)
        if scopes is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked token")
        return Principal(digest=digest, scopes=scopes)
    if is_trusted_client(request) and session_cookie_valid(request):
        return Principal(
            digest=trusted_client_digest(request),
            scopes={"read", "control", "timers"},
            trusted_network=True,
        )
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Bearer token or trusted home session required",
    )


def require_scope(scope: str, *, limit: int) -> object:
    async def dependency(
        request: Request, identity: Annotated[Principal, Depends(principal)]
    ) -> Principal:
        if scope not in identity.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Scope '{scope}' is required")
        limiter: RateLimiter = request.app.state.rate_limiter
        if not await limiter.allow(f"{identity.digest}:{scope}", limit):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
        return identity

    return Depends(dependency)
