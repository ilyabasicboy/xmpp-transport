import ast
import unittest
from pathlib import Path
from typing import Iterable, Set


ROOT = Path(__file__).parents[1]


def imported_roots(path: Path) -> Set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def python_files(directory: Path) -> Iterable[Path]:
    return directory.rglob("*.py")


class ArchitectureTests(unittest.TestCase):
    def test_domain_does_not_import_infrastructure(self) -> None:
        forbidden = {"aiohttp", "asyncpg", "slixmpp", "telethon"}
        for path in python_files(ROOT / "xmpp_transport" / "domain"):
            self.assertFalse(imported_roots(path) & forbidden, str(path))

    def test_application_and_ports_do_not_import_provider_adapters(self) -> None:
        for layer in ("application", "ports"):
            directory = ROOT / "xmpp_transport" / layer
            if not directory.exists():
                continue
            for path in python_files(directory):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("xmpp_transport.adapters.backends", source, str(path))

    def test_application_does_not_depend_on_runtime_or_adapters(self) -> None:
        forbidden = ("xmpp_transport.runtime", "xmpp_transport.adapters")
        for path in python_files(ROOT / "xmpp_transport" / "application"):
            source = path.read_text(encoding="utf-8")
            for dependency in forbidden:
                self.assertNotIn(dependency, source, str(path))


if __name__ == "__main__":
    unittest.main()
