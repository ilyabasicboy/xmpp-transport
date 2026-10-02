"""Executable CLI for a fault-isolated transport backend process."""

import argparse
import asyncio
import inspect
import os
import signal
import sys
from importlib import metadata
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from xmpp_transport.domain.identifiers import BackendId
from xmpp_transport.ports.backend import BackendPlugin

from .composition import SingleBackendRuntime, compose_single_backend
from .config import RuntimeConfig, load_config


BACKEND_ENTRY_POINT_GROUP = "xabber_transport.backends"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="xabber-transport")
    parser.add_argument(
        "--config",
        type=Path,
        help="INI path (defaults to XABBER_TRANSPORT_CONFIG or transports.ini)",
    )
    parser.add_argument("--backend", help="select one backend section")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate config and plugin wiring without opening connections",
    )
    return parser.parse_args(argv)


def selected_config(
    args: argparse.Namespace, environment: Optional[Mapping[str, str]] = None
) -> RuntimeConfig:
    source = os.environ if environment is None else environment
    configured_path = source.get("XABBER_TRANSPORT_CONFIG", "transports.ini")
    path = args.config if args.config is not None else Path(configured_path)
    config = load_config(path)
    if args.backend:
        matches = tuple(item for item in config.backends if item.name == args.backend)
        if not matches:
            raise ValueError("backend section not found in configuration: {}".format(args.backend))
        return RuntimeConfig(
            backends=matches,
            database=config.database,
            http=config.http,
            credential_key_env=config.credential_key_env,
            environment_file=config.environment_file,
            credential_key_value=config.credential_key_value,
        )
    if len(config.backends) != 1:
        raise ValueError("select one backend with --backend")
    return config


def discover_backend_plugins() -> Sequence[BackendPlugin]:
    available = metadata.entry_points()
    if hasattr(available, "select"):
        entries = available.select(group=BACKEND_ENTRY_POINT_GROUP)
    else:
        entries = available.get(BACKEND_ENTRY_POINT_GROUP, ())
    external = plugins_from_entry_points(entries)
    from xmpp_transport.adapters.backends.fake import FakeBackendPlugin
    from xmpp_transport.adapters.backends.max import MaxBackendPlugin
    from xmpp_transport.adapters.backends.telegram import TelegramBackendPlugin

    return (FakeBackendPlugin(), MaxBackendPlugin(), TelegramBackendPlugin()) + tuple(external)


def plugins_from_entry_points(entries: Iterable[object]) -> Sequence[BackendPlugin]:
    plugins = []
    seen = set()
    for entry in entries:
        loaded = entry.load()  # type: ignore[attr-defined]
        plugin = loaded() if inspect.isclass(loaded) else loaded
        backend_id = getattr(plugin, "backend_id", None)
        if not isinstance(backend_id, BackendId):
            raise TypeError("backend entry point must expose a BackendPlugin instance")
        if backend_id in seen:
            raise ValueError("duplicate backend plugin: {}".format(backend_id))
        seen.add(backend_id)
        plugins.append(plugin)
    return tuple(plugins)


def select_plugin(
    plugins: Sequence[BackendPlugin], backend_name: str
) -> BackendPlugin:
    backend_id = BackendId(backend_name)
    for plugin in plugins:
        if plugin.backend_id == backend_id:
            return plugin
    raise LookupError(
        "backend plugin is not installed: {} (entry point group: {})".format(
            backend_name, BACKEND_ENTRY_POINT_GROUP
        )
    )


async def serve_runtime(
    runtime: SingleBackendRuntime,
    shutdown_event: Optional[asyncio.Event] = None,
    install_signal_handlers: bool = True,
) -> None:
    stop = shutdown_event if shutdown_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    if install_signal_handlers:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
                installed.append(signum)
            except (NotImplementedError, RuntimeError):
                break
    try:
        await runtime.start()
        await stop.wait()
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
        await runtime.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        config = selected_config(args)
        plugins = discover_backend_plugins()
        backend = config.backends[0]
        plugin = select_plugin(plugins, backend.name)
        environment = config.resolved_environment()
        runtime = compose_single_backend(config, plugin, environment)
        if args.check_config:
            print("configuration valid for backend: {}".format(backend.name))
            return 0
        asyncio.run(serve_runtime(runtime))
        return 0
    except KeyboardInterrupt:
        return 130
    except (LookupError, TypeError, ValueError, ModuleNotFoundError) as exc:
        print("configuration error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
