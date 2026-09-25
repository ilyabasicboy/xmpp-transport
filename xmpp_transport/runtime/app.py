"""Command-line entry point and future application composition root."""

import argparse
from pathlib import Path
from typing import Optional, Sequence

from .config import BackendConfig, RuntimeConfig, load_config


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="xabber-transport")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--backend", help="run one registered backend")
    selection.add_argument("--config", type=Path, help="read backend instances from an INI file")
    parser.add_argument("--component-domain", help="component domain used with --backend")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> RuntimeConfig:
    if args.config is not None:
        return load_config(args.config)
    if not args.component_domain:
        raise ValueError("--component-domain is required with --backend")
    return RuntimeConfig(
        backends=(BackendConfig(args.backend, args.component_domain, {}),)
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = config_from_args(args)
    # Concrete adapters are deliberately composed here in subsequent phases.
    print("configured backend(s): {}".format(", ".join(item.name for item in config.backends)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

