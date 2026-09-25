"""Errors meaningful to application services and external adapters."""


class TransportError(Exception):
    """Base class for normalized transport failures."""


class BackendUnavailable(TransportError):
    pass


class AuthorizationRequired(TransportError):
    pass


class FeatureUnavailable(TransportError):
    pass


class InvalidCommand(TransportError):
    pass


class DuplicateOperation(TransportError):
    pass

