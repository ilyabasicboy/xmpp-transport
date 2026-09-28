"""INI configuration parsing without importing concrete adapters."""

import os
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True)
class BackendConfig:
    name: str
    component_domain: str
    options: Mapping[str, str]


@dataclass(frozen=True)
class DatabaseConfig:
    dsn: str = field(repr=False)
    min_pool_size: int = 1
    max_pool_size: int = 10
    command_timeout: float = 30.0

    def __post_init__(self) -> None:
        if not self.dsn.strip():
            raise ValueError("database DSN must not be empty")
        if self.min_pool_size < 1:
            raise ValueError("database min_pool_size must be positive")
        if self.max_pool_size < self.min_pool_size:
            raise ValueError("database max_pool_size must be at least min_pool_size")
        if self.command_timeout <= 0:
            raise ValueError("database command_timeout must be positive")


@dataclass(frozen=True)
class HttpConfig:
    host: str = "127.0.0.1"
    port: int = 8080

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("HTTP host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("HTTP port must be between 1 and 65535")


@dataclass(frozen=True)
class RuntimeConfig:
    backends: Sequence[BackendConfig]
    database: Optional[DatabaseConfig] = None
    http: HttpConfig = HttpConfig()
    credential_key_env: str = "XABBER_TRANSPORT_CREDENTIAL_KEY"

    def credential_key(self, environment: Optional[Mapping[str, str]] = None) -> bytes:
        source = os.environ if environment is None else environment
        value = source.get(self.credential_key_env)
        if value is None or not value.strip():
            raise ValueError(
                "credential encryption key environment variable is not set: {}".format(
                    self.credential_key_env
                )
            )
        try:
            return value.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("credential encryption key must be URL-safe base64") from None


def load_config(path: Path) -> RuntimeConfig:
    parser = ConfigParser()
    if not parser.read(str(path)):
        raise ValueError("configuration file not found: {}".format(path))

    backends = []
    for section in parser.sections():
        if not section.startswith("backend:"):
            continue
        name = section.partition(":")[2].strip()
        domain = _required(parser.get(section, "component_domain", fallback=None), section)
        options = dict(parser.items(section))
        options.pop("component_domain", None)
        backends.append(BackendConfig(name=name, component_domain=domain, options=options))

    if not backends:
        raise ValueError("configuration must contain at least one [backend:<name>] section")
    database = _database_config(parser)
    key_environment = parser.get(
        "security",
        "credential_key_env",
        fallback="XABBER_TRANSPORT_CREDENTIAL_KEY",
    ).strip()
    if not key_environment:
        raise ValueError("security.credential_key_env must not be empty")
    return RuntimeConfig(
        backends=tuple(backends),
        database=database,
        http=HttpConfig(
            host=parser.get("http", "host", fallback="127.0.0.1"),
            port=parser.getint("http", "port", fallback=8080),
        ),
        credential_key_env=key_environment,
    )


def _required(value: Optional[str], section: str) -> str:
    if value is None or not value.strip():
        raise ValueError("{} must define component_domain".format(section))
    return value.strip()


def _database_config(parser: ConfigParser) -> Optional[DatabaseConfig]:
    if not parser.has_section("database"):
        return None
    dsn = _required(parser.get("database", "dsn", fallback=None), "database")
    return DatabaseConfig(
        dsn=dsn,
        min_pool_size=parser.getint("database", "min_pool_size", fallback=1),
        max_pool_size=parser.getint("database", "max_pool_size", fallback=10),
        command_timeout=parser.getfloat("database", "command_timeout", fallback=30.0),
    )
