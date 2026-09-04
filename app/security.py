from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def write_private(path: Path, data: bytes) -> None:
    """Replace a private file atomically, including across process crashes."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def new_api_token() -> str:
    return f"hc_{secrets.token_urlsafe(32)}"


def token_hash(token: str, master_key: bytes) -> str:
    return hmac.new(master_key, token.encode(), hashlib.sha256).hexdigest()


def secret_matches(candidate: str, expected: bytes) -> bool:
    return hmac.compare_digest(candidate.encode(), expected)


class SecretBox:
    def __init__(self, master_key: bytes) -> None:
        derived = hashlib.sha256(b"haier-control/session/v1\0" + master_key).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(derived))

    def encrypt_json(self, payload: dict[str, Any]) -> bytes:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return self._fernet.encrypt(raw)

    def decrypt_json(self, ciphertext: bytes) -> dict[str, Any]:
        try:
            value = json.loads(self._fernet.decrypt(ciphertext))
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise ValueError("Encrypted session is invalid or uses a different key") from exc
        if not isinstance(value, dict):
            raise ValueError("Encrypted session must contain a JSON object")
        return value


SENSITIVE_KEYS = {
    "authorization",
    "access_token",
    "refresh_token",
    "id_token",
    "cognito-token",
    "cognito_token",
    "password",
    "email",
    "macaddress",
    "serialnumber",
    "serial",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
