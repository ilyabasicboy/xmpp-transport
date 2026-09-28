"""Deterministic backend used for local smoke tests and contract tests."""

from .plugin import FakeBackendPlugin

__all__ = ["FakeBackendPlugin"]

