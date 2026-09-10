"""Signed session-cookie helpers for CoreDesk.

Sessions are stored client-side in a single signed cookie. We use
itsdangerous to sign the payload so the cookie cannot be tampered with.
The signing secret comes from the COREDESK_SECRET environment variable and
falls back to a well-known development default when unset.
"""

import os

from itsdangerous import BadSignature, URLSafeSerializer

# Cookie name shared by the app when reading/writing the session.
COOKIE_NAME = "coredesk_session"

# Dev default is intentional -- set COREDESK_SECRET in any real deployment.
_SECRET = os.environ.get("COREDESK_SECRET", "dev-insecure-secret-change-me")

_serializer = URLSafeSerializer(_SECRET, salt="coredesk-session-v1")


def sign_session(data):
    """Serialize and sign a session dict into a cookie-safe string."""
    return _serializer.dumps(data)


def load_session(token):
    """Verify and deserialize a session token.

    Returns the session dict, or None if the token is missing or invalid.
    """
    if not token:
        return None
    try:
        return _serializer.loads(token)
    except BadSignature:
        return None
