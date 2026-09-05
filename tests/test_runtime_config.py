from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.runtime_config import load_runtime_config
from app.settings import Settings


def marker_settings(path: Path, **updates: object) -> Settings:
    return Settings(
        trusted_network_config_file=path,
        **updates,
    )


def write_marker(path: Path, content: str, mode: int = 0o600) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def test_trusted_marker_enables_mode_without_environment_values(tmp_path: Path) -> None:
    marker = tmp_path / "trusted-network.conf"
    write_marker(marker, "mode=trusted\ncidrs=192.0.2.0/24,100.64.0.0/10\n")

    settings = load_runtime_config(marker_settings(marker))

    assert settings.trusted_network_mode is True
    assert settings.trusted_network_cidrs == "192.0.2.0/24,100.64.0.0/10"


def test_explicit_environment_values_win_over_marker(tmp_path: Path) -> None:
    marker = tmp_path / "trusted-network.conf"
    write_marker(marker, "mode=trusted\ncidrs=192.0.2.0/24\n")

    settings = load_runtime_config(
        marker_settings(
            marker,
            trusted_network_mode=False,
            trusted_network_cidrs="203.0.113.0/24",
        )
    )

    assert settings.trusted_network_mode is False
    assert settings.trusted_network_cidrs == "203.0.113.0/24"


@pytest.mark.parametrize(
    "content,mode",
    [
        ("mode=trusted\n", 0o600),
        ("mode=trusted\ncidrs=192.0.2.0/24\nextra=value\n", 0o600),
        ("mode=trusted\ncidrs=192.0.2.0/24\n", 0o644),
    ],
)
def test_invalid_marker_fails_closed(tmp_path: Path, content: str, mode: int) -> None:
    marker = tmp_path / "trusted-network.conf"
    write_marker(marker, content, mode)

    with pytest.raises(RuntimeError):
        load_runtime_config(marker_settings(marker))
