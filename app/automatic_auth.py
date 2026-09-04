from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.settings import Settings


class AutomaticCredentialError(RuntimeError):
    pass


@dataclass(repr=False)
class AutomaticCredentials:
    email: str
    password: str
    source: Literal["files", "environment"]


def _read_private_file(path: Path, label: str) -> str:
    try:
        details = path.stat()
    except OSError as exc:
        raise AutomaticCredentialError(f"Configured {label} file is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(details.st_mode):
        raise AutomaticCredentialError(f"Configured {label} file is not a regular file")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise AutomaticCredentialError(f"Configured {label} file permissions are too broad")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise AutomaticCredentialError(f"Configured {label} file cannot be read") from exc
    if not value:
        raise AutomaticCredentialError(f"Configured {label} file is empty")
    return value


def load_automatic_credentials(settings: Settings) -> AutomaticCredentials | None:
    file_configured = settings.hon_email_file is not None or settings.hon_password_file is not None
    if file_configured:
        if settings.hon_email_file is None or settings.hon_password_file is None:
            raise AutomaticCredentialError("Both automatic credential files are required")
        return AutomaticCredentials(
            email=_read_private_file(settings.hon_email_file, "email"),
            password=_read_private_file(settings.hon_password_file, "password"),
            source="files",
        )

    env_configured = settings.hon_email is not None or settings.hon_password is not None
    if env_configured:
        if not settings.hon_email or settings.hon_password is None:
            raise AutomaticCredentialError("Both automatic environment credentials are required")
        password = settings.hon_password.get_secret_value()
        if not password:
            raise AutomaticCredentialError("Automatic environment password is empty")
        return AutomaticCredentials(
            email=settings.hon_email.strip(),
            password=password,
            source="environment",
        )
    return None


def clear_direct_credentials(settings: Settings) -> None:
    settings.hon_email = None
    settings.hon_password = None
    os.environ.pop("HAIER_HON_EMAIL", None)
    os.environ.pop("HAIER_HON_PASSWORD", None)
