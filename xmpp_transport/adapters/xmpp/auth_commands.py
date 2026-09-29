"""XMPP control-chat commands for provider authentication."""

import base64
import io
from dataclasses import dataclass, field
from typing import Sequence

import qrcode
import qrcode.image.svg

from xmpp_transport.application.authentication import AuthenticationCoordinator
from xmpp_transport.domain.auth import AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.identifiers import BackendId
from xmpp_transport.ports.repositories import BindingRepository

from .addressing import bare_jid


@dataclass(frozen=True)
class ControlMedia:
    name: str
    mime_type: str
    data_uri: str = field(repr=False)
    size: int


@dataclass(frozen=True)
class ControlResponse:
    body: str
    media: Sequence[ControlMedia] = field(default_factory=tuple)


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

    async def handle(self, from_jid: str, command: str) -> ControlResponse:
        value = command.strip()
        command_name, _, argument = value.partition(" ")
        command_name = command_name.lower()
        if command_name not in ("/login", "/continue", "/password"):
            return ControlResponse("Доступные команды: /login, /continue, /password <пароль>")
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
                return ControlResponse("Использование: /password <пароль>")
            challenge = await self._authentication.respond(
                binding.binding_id,
                self._backend_id,
                AuthResponse(AuthResponseKind.PASSWORD, argument),
            )
        if challenge.state is AuthState.WAITING_QR:
            if not challenge.public_url:
                return ControlResponse("MAX не вернул данные для QR-кода.")
            return ControlResponse(
                "Отсканируйте QR-код приложением MAX.\n"
                "После сканирования отправьте /continue.",
                (_qr_svg(challenge.public_url),),
            )
        if challenge.state is AuthState.WAITING_PASSWORD:
            return ControlResponse("MAX запросил пароль 2FA. Отправьте /password <пароль>.")
        if challenge.state is AuthState.CONNECTED:
            return ControlResponse("MAX успешно подключён.")
        return ControlResponse(challenge.message or "Авторизация MAX завершилась с ошибкой.")


def _qr_svg(value: str) -> ControlMedia:
    image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    image.save(stream)
    content = stream.getvalue()
    encoded = base64.b64encode(content).decode("ascii")
    return ControlMedia(
        name="max-login-qr.svg",
        mime_type="image/svg+xml",
        data_uri="data:image/svg+xml;base64,{}".format(encoded),
        size=len(content),
    )
