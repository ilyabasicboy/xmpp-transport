"""XMPP control-chat commands for starting provider authentication."""

from typing import Protocol

from xmpp_transport.domain.identifiers import BackendId, BindingId
from xmpp_transport.ports.repositories import BindingRepository

from .addressing import bare_jid


class AuthenticationAttempt(Protocol):
    public_url: str


class AuthenticationAttemptIssuer(Protocol):
    async def create_attempt(
        self,
        binding_id: BindingId,
        backend_id: BackendId,
        public_base_url: str,
    ) -> AuthenticationAttempt:
        ...


class XmppAuthenticationCommands:
    def __init__(
        self,
        backend_id: BackendId,
        component_domain: str,
        public_base_url: str,
        bindings: BindingRepository,
        attempts: AuthenticationAttemptIssuer,
    ) -> None:
        self._backend_id = backend_id
        self._component_domain = component_domain
        self._public_base_url = public_base_url
        self._bindings = bindings
        self._attempts = attempts

    def accepts(self, to_jid: str) -> bool:
        return to_jid.split("/", 1)[0].strip().lower() == self._component_domain

    async def handle(self, from_jid: str, command: str) -> str:
        if command.strip().lower() != "/login":
            return "Доступная команда: /login"
        owner = bare_jid(from_jid)
        binding = await self._bindings.binding_for_authentication(owner, self._backend_id)
        if binding is None:
            raise LookupError("binding is not available for authentication")
        attempt = await self._attempts.create_attempt(
            binding.binding_id,
            self._backend_id,
            self._public_base_url,
        )
        return "Откройте безопасную ссылку для входа в MAX: {}".format(
            attempt.public_url
        )
