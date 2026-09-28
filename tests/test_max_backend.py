import unittest

from xmpp_transport.adapters.backends.max import MaxBackendPlugin, MaxCredentials
from xmpp_transport.domain.identifiers import BackendId, BindingId


class MaxCredentialsTests(unittest.TestCase):
    def test_round_trips_credentials_without_exposing_secrets(self) -> None:
        credentials = MaxCredentials("secret-token", "secret-device", "42")

        self.assertEqual(credentials, MaxCredentials.decode(credentials.encode()))
        self.assertNotIn("secret-token", repr(credentials))
        self.assertNotIn("secret-device", repr(credentials))

    def test_rejects_missing_or_invalid_fields(self) -> None:
        invalid = (b"not-json", b"[]", b'{}', b'{"token":"x"}')
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                MaxCredentials.decode(payload)


class MaxBackendPluginTests(unittest.TestCase):
    def test_identifies_as_max_and_validates_session_credentials(self) -> None:
        plugin = MaxBackendPlugin()
        self.assertEqual(BackendId("max"), plugin.backend_id)

        with self.assertRaises(ValueError):
            plugin.create_session(BindingId("binding-1"), b"invalid", object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
