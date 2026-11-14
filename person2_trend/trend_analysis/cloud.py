"""Address and login for the shared cloud collection API."""

import base64
import os


CLOUD_BASE_URL = "https://43.161.240.54"


def resolve_base_url(explicit=None):
    """Return the cloud API unless a caller or STEAM_API_BASE_URL overrides it."""
    if explicit:
        return explicit.rstrip("/")
    configured = os.environ.get("STEAM_API_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return CLOUD_BASE_URL


def is_local(base_url):
    lowered = base_url.lower()
    return "127.0.0.1" in lowered or "localhost" in lowered


def basic_authorization():
    """HTTP Basic header from STEAM_API_USER and STEAM_API_PASSWORD, or None."""
    user = os.environ.get("STEAM_API_USER", "")
    password = os.environ.get("STEAM_API_PASSWORD", "")
    if not user or not password:
        return None
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return "Basic " + token


def require_authorization(base_url):
    """Cloud requests need the group login. Localhost stays open for sample mode."""
    header = basic_authorization()
    if header is None and not is_local(base_url):
        raise RuntimeError(
            "Set STEAM_API_USER and STEAM_API_PASSWORD before reading the cloud collection API"
        )
    return header
