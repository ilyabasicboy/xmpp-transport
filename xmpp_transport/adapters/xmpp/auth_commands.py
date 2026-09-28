"""XMPP control-chat commands for provider authentication."""

from xmpp_transport.application.authentication import AuthenticationCoordinator
from xmpp_transport.domain.auth import AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.identifiers import BackendId
from xmpp_transport.ports.repositories import BindingRepository

from .addressing import bare_jid


class XmppAuthenticationCommands:
    def __init__(
        self,
        backend_id: BackendId,
        component_domain: str,
        bindings: BindingRepository,
        authentication: AuthenticationCoordinator,
        control_localpart: str = "bot",
    ) -> None:
        self._backend_id = backend_id
        localpart = control_localpart.strip().lower()
        if not localpart or "@" in localpart or "/" in localpart:
            raise ValueError("control localpart is invalid")
        self._control_jid = "{}@{}".format(
            localpart, component_domain.strip().lower()
        )
        self._bindings = bindings
        self._authentication = authentication

    def accepts(self, to_jid: str) -> bool:
        return to_jid.split("/", 1)[0].strip().lower() == self._control_jid

    async def handle(self, from_jid: str, command: str) -> str:
        value = command.strip()
        command_name, _, argument = value.partition(" ")
        command_name = command_name.lower()
        if command_name not in ("/login", "/continue", "/password"):
            return "Доступные команды: /login, /continue, /password <пароль>"
        owner = bare_jid(from_jid)
        if command_name == "/login":
            binding = await self._bindings.ensure_binding(owner, self._backend_id)
            challenge = await self._authentication.begin(
                binding.binding_id, self._backend_id
            )
        else:
            binding = await self._bindings.binding_for_authentication(
                owner, self._backend_id
            )
            if binding is None:
                raise LookupError("binding is not available for authentication")
        if command_name == "/continue":
            challenge = await self._authentication.respond(
                binding.binding_id,
                self._backend_id,
                AuthResponse(AuthResponseKind.CONFIRMATION, "confirmed"),
            )
        elif command_name == "/password":
            if not argument:
                return "Использование: /password <пароль>"
            challenge = await self._authentication.respond(
                binding.binding_id,
                self._backend_id,
                AuthResponse(AuthResponseKind.PASSWORD, argument),
            )
        if challenge.state is AuthState.WAITING_QR:
            return (
                "Откройте ссылку и отсканируйте QR-код приложением MAX: {}\n"
                "После сканирования отправьте /continue."
            ).format(challenge.public_url or "")
        if challenge.state is AuthState.WAITING_PASSWORD:
            return "MAX запросил пароль 2FA. Отправьте /password <пароль>."
        if challenge.state is AuthState.CONNECTED:
            return "MAX успешно подключён."
        return challenge.message or "Авторизация MAX завершилась с ошибкой."
