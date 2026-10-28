"""Authenticated, user-bound credential encryption with an ordered rotation keyring."""

import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.errors import ConfigurationError, GraftError


class ReconnectRequired(GraftError):
    code = "github_reconnect_required"


class CredentialCipher:
    def __init__(self, keys: str):
        try:
            self.fernet = MultiFernet([Fernet(key.strip()) for key in keys.split(",")])
        except (ValueError, TypeError):
            raise ConfigurationError("Configure valid credential encryption keys.") from None

    def encrypt(self, owner: str, data: dict) -> bytes:
        return self.fernet.encrypt(json.dumps({"owner": owner, "data": data}).encode())

    def decrypt(self, owner: str, raw: bytes) -> dict:
        try:
            value = json.loads(self.fernet.decrypt(raw))
            if value["owner"] != owner or not isinstance(value["data"], dict):
                raise ValueError("Owner mismatch")
            return value["data"]
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise ReconnectRequired(
                "GitHub credentials are unavailable. Reconnect GitHub."
            ) from None
