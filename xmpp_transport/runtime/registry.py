from typing import Dict, Iterable

from xmpp_transport.domain.identifiers import BackendId
from xmpp_transport.ports.backend import BackendPlugin


class BackendRegistry:
    def __init__(self, plugins: Iterable[BackendPlugin] = ()) -> None:
        self._plugins: Dict[BackendId, BackendPlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: BackendPlugin) -> None:
        if plugin.backend_id in self._plugins:
            raise ValueError("backend already registered: {}".format(plugin.backend_id))
        self._plugins[plugin.backend_id] = plugin

    def get(self, backend_id: BackendId) -> BackendPlugin:
        try:
            return self._plugins[backend_id]
        except KeyError as exc:
            raise LookupError("unknown backend: {}".format(backend_id)) from exc

