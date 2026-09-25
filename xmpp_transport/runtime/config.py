"""INI configuration parsing without importing concrete adapters."""

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True)
class BackendConfig:
    name: str
    component_domain: str
    options: Mapping[str, str]


@dataclass(frozen=True)
class RuntimeConfig:
    backends: Sequence[BackendConfig]


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
    return RuntimeConfig(backends=tuple(backends))


def _required(value: Optional[str], section: str) -> str:
    if value is None or not value.strip():
        raise ValueError("{} must define component_domain".format(section))
    return value.strip()

