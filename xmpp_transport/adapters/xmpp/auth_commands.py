"""XMPP control-chat commands for provider authentication."""

import base64
import io
from dataclasses import dataclass, field
from typing import Optional, Sequence

import qrcode
import qrcode.image.svg

from xmpp_transport.application.authentication import AuthenticationCoordinator
from xmpp_transport.domain.auth import AuthChallenge, AuthResponse, AuthResponseKind, AuthState
from xmpp_transport.domain.identifiers import BackendId, BindingId
from xmpp_transport.ports.backend import BackendFeatureProvider, ContactAdder, ContactSource
from xmpp_transport.ports.repositories import BindingRepository
from xmpp_transport.ports.xmpp import XmppRoster

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
        sessions: Optional[BackendFeatureProvider] = None,
        roster: Optional[XmppRoster] = None,
        contacts_page_size: int = 20,
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
        self._sessions = sessions
        self._roster = roster
        self._contacts_page_size = contacts_page_size

    def accepts(self, to_jid: str) -> bool:
        return to_jid.split("/", 1)[0].strip().lower() == self._control_jid

    async def handle(self, from_jid: str, command: str) -> ControlResponse:
        value = command.strip()
        command_name, _, argument = value.partition(" ")
        command_name = command_name.lower()
        owner = bare_jid(from_jid)
        if command_name in ("/help", "/?") or not command_name.startswith("/"):
            return ControlResponse(self.help_text())
        if command_name == "/status":
            return ControlResponse(await self._status(owner))
        if command_name == "/logout":
            return ControlResponse(await self._logout(owner))
        if command_name == "/contacts":
            return ControlResponse(await self._contacts(owner, argument))
        if command_name == "/add":
            return ControlResponse(await self._add(owner, argument))
        if command_name not in ("/login", "/password"):
            return ControlResponse("Неизвестная команда.\n\n" + self.help_text())
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
        if command_name == "/password":
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
                "После подтверждения transport сообщит о результате здесь.",
                (_qr_svg(challenge.public_url),),
            )
        if challenge.state is AuthState.WAITING_PASSWORD:
            return ControlResponse("MAX запросил пароль 2FA. Отправьте /password <пароль>.")
        if challenge.state is AuthState.CONNECTED:
            return ControlResponse("MAX успешно подключён.")
        return ControlResponse(challenge.message or "Авторизация MAX завершилась с ошибкой.")

    async def _active(self, owner: str):  # type: ignore[no-untyped-def]
        binding = await self._bindings.binding_for_authentication(owner, self._backend_id)
        if binding is None or self._sessions is None:
            return binding, None
        feature = await self._sessions.feature(binding.binding_id, ContactSource)
        return binding, feature

    async def _status(self, owner: str) -> str:
        binding, contacts = await self._active(owner)
        if binding is None:
            return "MAX не подключен. Отправьте /login для авторизации."
        if contacts is not None:
            return "MAX подключен."
        return "MAX-сессия сохранена, но сейчас не подключена."

    async def _contacts(self, owner: str, argument: str) -> str:
        binding, source = await self._active(owner)
        if binding is None or source is None:
            return "MAX не подключен. Отправьте /login для авторизации."
        try:
            page = int(argument) if argument else 1
        except ValueError:
            return "Номер страницы должен быть целым числом."
        if page < 1:
            return "Используйте: /contacts [страница]"
        contacts = tuple(await source.contacts())
        if not contacts:
            return "В MAX нет сохраненных контактов."
        start = (page - 1) * self._contacts_page_size
        if start >= len(contacts):
            return "Такой страницы контактов нет."
        shown = contacts[start : start + self._contacts_page_size]
        total = (len(contacts) + self._contacts_page_size - 1) // self._contacts_page_size
        lines = ["Контакты MAX, страница {}/{}:".format(page, total)]
        lines.extend(
            "{}. {}".format(start + index, contact.display_name)
            for index, contact in enumerate(shown, start=1)
        )
        lines.extend(("", "Добавить в Xabber: /add <номер>"))
        if page < total:
            lines.append("Следующая страница: /contacts {}".format(page + 1))
        lines.append("Добавить по телефону: /add phone +79990000000")
        return "\n".join(lines)

    async def _add(self, owner: str, argument: str) -> str:
        binding, source = await self._active(owner)
        if binding is None or source is None or self._roster is None:
            return "MAX не подключен. Отправьте /login для авторизации."
        if argument.lower().startswith("phone "):
            adder = await self._sessions.feature(binding.binding_id, ContactAdder)  # type: ignore[union-attr]
            if adder is None:
                return "Добавление контакта по телефону недоступно."
            phone = argument[6:].strip()
            if not phone:
                return "Используйте: /add phone +79990000000"
            contact = await adder.add_contact_by_phone(phone)
        else:
            try:
                selection = int(argument)
            except ValueError:
                return "Используйте: /add <номер> или /add phone +79990000000"
            contacts = tuple(await source.contacts())
            if selection < 1 or selection > len(contacts):
                return "Контакт с таким номером отсутствует в списке."
            contact = contacts[selection - 1]
        await self._roster.add_contact(binding.binding_id, contact)
        return "Контакт добавлен в Xabber: {}".format(contact.display_name)

    async def _logout(self, owner: str) -> str:
        binding = await self._bindings.binding_for_authentication(owner, self._backend_id)
        if binding is None:
            return "MAX не подключен. Отправьте /login для авторизации."
        await self._authentication.cancel(binding.binding_id)
        if self._sessions is not None:
            await self._sessions.stop(binding.binding_id)  # type: ignore[attr-defined]
        await self._bindings.disable_binding(binding.binding_id)
        return "MAX отключен, сохраненная сессия удалена."

    @staticmethod
    def help_text() -> str:
        return (
            "Команды MAX transport:\n"
            "/login - подключить MAX-аккаунт через QR\n"
            "/password <пароль> - продолжить login при включенной 2FA\n"
            "/status - проверить состояние подключения\n"
            "/contacts [страница] - показать контакты MAX\n"
            "/add <номер> - добавить выбранный контакт в Xabber\n"
            "/add phone +79990000000 - добавить контакт MAX по телефону\n"
            "/logout - отключить MAX и удалить сохраненную сессию\n"
            "/help - показать команды"
        )


class XmppAuthenticationNotices:
    def __init__(
        self,
        control_jid: str,
        bindings: BindingRepository,
        wire,  # type: ignore[no-untyped-def]
        codec,  # type: ignore[no-untyped-def]
    ) -> None:
        self._control_jid = control_jid
        self._bindings = bindings
        self._wire = wire
        self._codec = codec

    async def deliver(self, binding_id: BindingId, challenge: AuthChallenge) -> None:
        owner_jid = await self._bindings.xmpp_account_for_authentication(binding_id)
        if owner_jid is None:
            raise LookupError("XMPP account not found for authentication binding")
        if challenge.state is AuthState.WAITING_PASSWORD:
            body = "MAX запросил пароль 2FA. Отправьте /password <пароль>."
        elif challenge.state is AuthState.CONNECTED:
            body = "MAX успешно подключён."
        else:
            body = challenge.message or "Авторизация MAX завершилась с ошибкой."
        await self._wire.send(
            self._codec.control_notice(
                self._control_jid, owner_jid, ControlResponse(body)
            )
        )


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
