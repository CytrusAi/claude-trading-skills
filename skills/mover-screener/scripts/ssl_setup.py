#!/usr/bin/env python3
"""
SSL setup for mover-screener.

On macOS the system Python / urllib / requests do not find a CA bundle by
default, which makes finvizfinance (and any HTTPS call) fail with
SSL: CERTIFICATE_VERIFY_FAILED. Importing this module sets the certifi bundle
into the environment BEFORE finvizfinance / requests build their SSL contexts.

Import this module first, at the top of every entry point.
"""

import os

try:
    import certifi

    _CA = certifi.where()
    # finvizfinance reads these env vars when it constructs its requests session.
    os.environ.setdefault("SSL_CERT_FILE", _CA)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _CA)
    HAS_CERTIFI = True
except ImportError:  # pragma: no cover - certifi is a finvizfinance dependency
    _CA = None
    HAS_CERTIFI = False


def ca_bundle():
    """Return the certifi CA bundle path, or None if certifi is unavailable."""
    return _CA
