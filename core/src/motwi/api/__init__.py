"""The local API: the service as JSON over HTTP and one WebSocket of events, on 127.0.0.1."""

from motwi.api.server import DEFAULT_PORT, ApiAuth, create_app, serve

__all__ = ["DEFAULT_PORT", "ApiAuth", "create_app", "serve"]
