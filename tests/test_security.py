import unittest

from cryptography.fernet import Fernet

from xmpp_transport.adapters.security import CredentialDecryptionError, FernetCredentialCipher


class FernetCredentialCipherTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        cipher = FernetCredentialCipher(Fernet.generate_key())
        encrypted = cipher.encrypt(b"provider-session")
        self.assertNotIn(b"provider-session", encrypted)
        self.assertEqual(b"provider-session", cipher.decrypt(encrypted))

    def test_wrong_key_is_normalized_without_token_disclosure(self) -> None:
        encrypted = FernetCredentialCipher(Fernet.generate_key()).encrypt(b"private")
        cipher = FernetCredentialCipher(Fernet.generate_key())
        with self.assertRaises(CredentialDecryptionError) as context:
            cipher.decrypt(encrypted)
        self.assertNotIn(encrypted.decode("ascii"), str(context.exception))

    def test_invalid_key_is_normalized(self) -> None:
        with self.assertRaisesRegex(ValueError, "key is invalid"):
            FernetCredentialCipher(b"not-a-fernet-key")

    def test_empty_plaintext_is_rejected(self) -> None:
        cipher = FernetCredentialCipher(Fernet.generate_key())
        with self.assertRaises(ValueError):
            cipher.encrypt(b"")


if __name__ == "__main__":
    unittest.main()
