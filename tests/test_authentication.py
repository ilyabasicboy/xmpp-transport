import unittest

from xmpp_transport.application.authentication import AuthenticationCoordinator
from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.identifiers import BackendId, BindingId


class Flow:
    def __init__(self) -> None:
        self.closed = 0

    async def start(self) -> AuthChallenge:
        return AuthChallenge(AuthState.WAITING_QR, public_url="https://example.test/qr")

    async def respond(self, response: AuthResponse) -> AuthChallenge:
        return AuthChallenge(AuthState.CONNECTED)

    def credentials(self) -> bytes:
        return b"private"

    async def close(self) -> None:
        self.closed += 1


class Plugin:
    backend_id = BackendId("max")

    def __init__(self) -> None:
        self.flows = []

    def create_authentication(self, binding_id):  # type: ignore[no-untyped-def]
        flow = Flow()
        self.flows.append(flow)
        return flow


class Registry:
    def __init__(self, plugin: Plugin) -> None:
        self.plugin = plugin

    def get(self, backend_id):  # type: ignore[no-untyped-def]
        return self.plugin


class Bindings:
    def __init__(self) -> None:
        self.saved = []

    async def save_encrypted_credentials(self, binding_id, backend_id, credentials):  # type: ignore[no-untyped-def]
        self.saved.append((binding_id, backend_id, credentials))


class Cipher:
    def encrypt(self, value: bytes) -> bytes:
        return b"encrypted:" + value


class Sessions:
    def __init__(self) -> None:
        self.started = []

    async def start(self, binding_id, backend_id):  # type: ignore[no-untyped-def]
        self.started.append((binding_id, backend_id))
        return object()


class AuthenticationCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.plugin = Plugin()
        self.bindings = Bindings()
        self.sessions = Sessions()
        self.coordinator = AuthenticationCoordinator(
            Registry(self.plugin),  # type: ignore[arg-type]
            self.bindings,  # type: ignore[arg-type]
            Cipher(),  # type: ignore[arg-type]
            self.sessions,
        )

    async def test_encrypts_persists_and_starts_connected_binding(self) -> None:
        binding_id = BindingId("binding-1")
        backend_id = BackendId("max")

        challenge = await self.coordinator.begin(binding_id, backend_id)
        connected = await self.coordinator.respond(
            binding_id,
            backend_id,
            AuthResponse(AuthResponseKind.CONFIRMATION, "opaque-confirmation"),
        )

        self.assertEqual(AuthState.WAITING_QR, challenge.state)
        self.assertEqual(AuthState.CONNECTED, connected.state)
        self.assertEqual(
            [(binding_id, backend_id, b"encrypted:private")], self.bindings.saved
        )
        self.assertEqual([(binding_id, backend_id)], self.sessions.started)
        self.assertEqual(1, self.plugin.flows[0].closed)

    async def test_restarting_flow_closes_previous_one(self) -> None:
        binding_id = BindingId("binding-1")
        backend_id = BackendId("max")
        await self.coordinator.begin(binding_id, backend_id)
        first = self.plugin.flows[0]

        await self.coordinator.begin(binding_id, backend_id)

        self.assertEqual(1, first.closed)

    async def test_close_closes_active_flows(self) -> None:
        await self.coordinator.begin(BindingId("binding-1"), BackendId("max"))
        flow = self.plugin.flows[0]

        await self.coordinator.close()

        self.assertEqual(1, flow.closed)
