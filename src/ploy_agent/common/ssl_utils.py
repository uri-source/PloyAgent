"""TLS for outbound HTTPS/WSS. Prefer OS trust (truststore), then certifi; optional dev bypass."""

from __future__ import annotations

import os
import ssl
from functools import lru_cache


def _insecure_tls_requested() -> bool:
    return os.environ.get("PLOY_INSECURE_SSL", "").strip().lower() in ("1", "true", "yes")


def _insecure_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@lru_cache
def _trusted_ssl_context() -> ssl.SSLContext:
    try:
        import truststore

        return truststore.ssl_context()
    except Exception:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())


def ssl_context() -> ssl.SSLContext:
    """Shared context for WebSockets (always an SSLContext)."""
    if _insecure_tls_requested():
        return _insecure_context()
    return _trusted_ssl_context()


def httpx_verify() -> ssl.SSLContext | str | bool:
    """For httpx.AsyncClient(verify=...)."""
    if _insecure_tls_requested():
        return False
    try:
        return _trusted_ssl_context()
    except Exception:
        try:
            import certifi

            return certifi.where()
        except Exception:
            return True


def websocket_ssl_context() -> ssl.SSLContext:
    """For websockets.connect(ssl=...)."""
    return ssl_context()
