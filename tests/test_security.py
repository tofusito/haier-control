from __future__ import annotations

import pytest

from app.security import SecretBox, redact, token_hash


def test_secret_box_round_trip_and_wrong_key() -> None:
    box = SecretBox(b"a" * 40)
    ciphertext = box.encrypt_json({"refresh_token": "secret", "mobile_id": "device"})
    assert b"secret" not in ciphertext
    assert box.decrypt_json(ciphertext)["refresh_token"] == "secret"
    with pytest.raises(ValueError):
        SecretBox(b"b" * 40).decrypt_json(ciphertext)


def test_token_hash_is_keyed() -> None:
    assert token_hash("hc_example", b"a" * 32) != token_hash("hc_example", b"b" * 32)


def test_recursive_redaction() -> None:
    value = redact({"email": "person@example.invalid", "nested": {"id_token": "abc"}})
    assert value == {"email": "[REDACTED]", "nested": {"id_token": "[REDACTED]"}}
