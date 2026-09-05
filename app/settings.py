from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HAIER_", extra="ignore")

    driver: str = Field(default="mock", pattern="^(mock|haier-cloud)$")
    database_path: Path = Path("/data/haier-control.db")
    master_key_file: Path = Path("/run/secrets/haier_control_master_key")
    bootstrap_token_file: Path = Path("/run/secrets/haier_control_bootstrap_token")
    encrypted_session_file: Path = Path("/data/haier-session.enc")
    bind_host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    log_level: str = "INFO"
    poll_interval_seconds: int = Field(default=30, ge=10, le=600)
    command_dedupe_seconds: int = Field(default=3, ge=1, le=30)
    trusted_network_mode: bool = False
    trusted_network_cidrs: str = ""
    trusted_network_config_file: Path = Path("/data/haier-trusted-network.conf")
    trusted_session_ttl_seconds: int = Field(default=2_592_000, ge=300, le=31_536_000)
    haier_client_id: str = (
        "3MVG9QDx8IX8nP5T2Ha8ofvlmjLZl5L_gvfbT9."
        "HJvpHGKoAS_dcMN8LYpTSYeVFCraUnV.2Ag1Ki7m4znVO6"
    )
    hon_email: str | None = None
    hon_password: SecretStr | None = None
    hon_email_file: Path | None = None
    hon_password_file: Path | None = None


def load_secret(path: Path, *, required: bool = True) -> bytes:
    try:
        value = path.read_bytes().strip()
    except FileNotFoundError:
        if required:
            raise RuntimeError(f"Required secret file is missing: {path}") from None
        return b""
    if required and len(value) < 32:
        raise RuntimeError(f"Secret file must contain at least 32 bytes: {path}")
    return value
