from __future__ import annotations

import stat

from app.settings import Settings


def load_runtime_config(settings: Settings) -> Settings:
    """Load the optional host-only trusted-network switch.

    The marker exists for deployments whose Compose file is managed by another
    administrator (for example a root-owned DockerHand stack). It contains no
    secret and is ignored when an operator explicitly supplied either trusted
    setting through the environment or constructor.
    """

    path = settings.trusted_network_config_file
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return settings
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError(
            f"Trusted network config must be a regular 0600 file: {path}"
        )
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in {"mode", "cidrs"} or key in values:
            raise RuntimeError(f"Invalid trusted network config: {path}")
        values[key] = value.strip()
    if values.get("mode") != "trusted" or not values.get("cidrs"):
        raise RuntimeError(f"Trusted network config is incomplete: {path}")

    supplied = settings.model_fields_set
    updates: dict[str, object] = {}
    if "trusted_network_mode" not in supplied:
        updates["trusted_network_mode"] = True
    if "trusted_network_cidrs" not in supplied:
        updates["trusted_network_cidrs"] = values["cidrs"]
    return settings.model_copy(update=updates) if updates else settings
