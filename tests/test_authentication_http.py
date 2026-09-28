import unittest
from datetime import datetime, timezone

from xmpp_transport.adapters.web.authentication import AiohttpAuthenticationApi
from xmpp_transport.domain.auth import AuthChallenge, AuthState
from xmpp_transport.domain.identifiers import BackendId, BindingId


class Coordinator:
    def __init__(self) -> None:
        self.responses = []
        self.cancelled = []

    async def begin(self, binding_id, backend_id):  # type: ignore[no-untyped-def]
        return AuthChallenge(
            AuthState.WAITING_QR,
            expires_at=datetime.now(timezone.utc),
            public_url="https://max.example/scan/opaque",
        )

    async def respond(self, binding_id, backend_id, response):  # type: ignore[no-untyped-def]
        self.responses.append((binding_id, backend_id, response))
        return AuthChallenge(AuthState.CONNECTED, message="connected")

    async def cancel(self, binding_id):  # type: ignore[no-untyped-def]
        self.cancelled.append(binding_id)


class Router:
    def __init__(self) -> None:
        self.get = {}
        self.post = {}

    def add_get(self, path, handler):  # type: ignore[no-untyped-def]
        self.get[path] = handler

    def add_post(self, path, handler):  # type: ignore[no-untyped-def]
        self.post[path] = handler


class Application:
    def __init__(self) -> None:
        self.router = Router()


class Web:
    @staticmethod
    def json_response(payload, status):  # type: ignore[no-untyped-def]
        return {"payload": payload, "status": status}


class Request:
    def __init__(self, token, payload=None):  # type: ignore[no-untyped-def]
        self.match_info = {"token": token}
        self.payload = payload

    async def json(self):  # type: ignore[no-untyped-def]
        return self.payload


class AuthenticationHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = 100.0
        self.coordinator = Coordinator()
        self.api = AiohttpAuthenticationApi(
            self.coordinator,  # type: ignore[arg-type]
            token_ttl=30,
            clock=lambda: self.now,
        )
        self.application = Application()
        self.api.register(self.application, Web())

    async def test_creates_opaque_https_attempt_and_reports_qr_state(self) -> None:
        attempt = await self.api.create_attempt(
            BindingId("private-binding"), BackendId("max"), "https://transport.example"
        )
        token = attempt.public_url.rsplit("/", 1)[1]
        response = await self.application.router.get["/auth/{token}"](Request(token))

        self.assertNotIn("private-binding", attempt.public_url)
        self.assertEqual(200, response["status"])
        self.assertEqual("waiting_qr", response["payload"]["state"])
        self.assertEqual(
            "https://max.example/scan/opaque", response["payload"]["provider_url"]
        )

    async def test_password_is_forwarded_in_post_body_and_attempt_is_consumed(self) -> None:
        attempt = await self.api.create_attempt(
            BindingId("binding-1"), BackendId("max"), "https://transport.example/"
        )
        token = attempt.public_url.rsplit("/", 1)[1]
        handler = self.application.router.post["/auth/{token}"]

        response = await handler(Request(token, {"kind": "password", "secret": "private"}))
        repeated = await handler(Request(token, {"kind": "password", "secret": "private"}))

        self.assertEqual("connected", response["payload"]["state"])
        self.assertNotIn("private", str(response))
        self.assertEqual(404, repeated["status"])
        self.assertEqual("private", self.coordinator.responses[0][2].secret)

    async def test_expired_attempt_is_cancelled(self) -> None:
        attempt = await self.api.create_attempt(
            BindingId("binding-1"), BackendId("max"), "https://transport.example"
        )
        token = attempt.public_url.rsplit("/", 1)[1]
        self.now = 131.0

        response = await self.application.router.get["/auth/{token}"](Request(token))

        self.assertEqual(404, response["status"])
        self.assertEqual([BindingId("binding-1")], self.coordinator.cancelled)

    async def test_rejects_non_https_public_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            await self.api.create_attempt(
                BindingId("binding-1"), BackendId("max"), "http://transport.example"
            )
