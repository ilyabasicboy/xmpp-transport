"""Authenticated encryption for provider credential payloads."""

from cryptography.fernet import Fernet, InvalidToken

from xmpp_transport.domain.errors import TransportError


class CredentialDecryptionError(TransportError):
    """Stored credentials cannot be authenticated or decrypted."""


class FernetCredentialCipher:
    def __init__(self, key: bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except (TypeError, ValueError) as exc:
            raise ValueError("credential encryption key is invalid") from exc

    def encrypt(self, plaintext: bytes) -> bytes:
        if not plaintext:
            raise ValueError("credential payload must not be empty")
        return self._fernet.encrypt(plaintext)

    def decrypt(self, encrypted: bytes) -> bytes:
        if not encrypted:
            raise CredentialDecryptionError("stored credential payload is invalid")
        try:
            return self._fernet.decrypt(encrypted)
        except InvalidToken as exc:
            raise CredentialDecryptionError("stored credential payload is invalid") from exc
