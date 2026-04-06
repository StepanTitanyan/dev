import time
import logging
import requests

from rise.config.config import settings

logger = logging.getLogger(__name__)

# Module-level token cache — shared across all calls within the worker process
_cached_token: str | None = None
_token_expiry: float = 0.0


def _get_access_token() -> str:
    """
    Obtain a Salesforce access token using the OAuth2 client_credentials grant.
    Token is cached in memory and refreshed automatically when it expires.
    """
    global _cached_token, _token_expiry

    if _cached_token and time.time() < _token_expiry:
        return _cached_token

    if not settings.SALESFORCE_INSTANCE_URL:
        raise RuntimeError("SALESFORCE_INSTANCE_URL is not configured")
    if not settings.SALESFORCE_CLIENT_ID:
        raise RuntimeError("SALESFORCE_CLIENT_ID is not configured")
    if not settings.SALESFORCE_CLIENT_SECRET:
        raise RuntimeError("SALESFORCE_CLIENT_SECRET is not configured")

    logger.info("[SALESFORCE] Obtaining access token via client_credentials")

    response = requests.post(
        f"{settings.SALESFORCE_INSTANCE_URL}/services/oauth2/token",
        params={
            "grant_type": "client_credentials",
            "client_id": settings.SALESFORCE_CLIENT_ID,
            "client_secret": settings.SALESFORCE_CLIENT_SECRET,
        },
        timeout=20)

    if not response.ok:
        logger.error(
            "[SALESFORCE] Token request failed: status=%s body=%s",
            response.status_code, response.text[:500])
        response.raise_for_status()

    data = response.json()
    _cached_token = data["access_token"]

    # Salesforce client_credentials tokens are typically valid for 2 hours.
    # We expire the cache 5 minutes early to avoid using a token right as it expires.
    expires_in = data.get("expires_in", 7200)
    _token_expiry = time.time() + max(int(expires_in) - 300, 60)

    logger.info("[SALESFORCE] Access token obtained successfully")
    return _cached_token


def _invalidate_token() -> None:
    global _cached_token, _token_expiry
    _cached_token = None
    _token_expiry = 0.0


def download_content_version(content_version_id: str) -> bytes:
    """
    Download the binary content of a Salesforce ContentVersion record.

    Uses:
        GET /services/data/vXX.X/sobjects/ContentVersion/{id}/VersionData

    Returns the raw file bytes. Supports files up to 2 GB (Salesforce REST API limit).
    Retries once automatically if the token has expired (401).
    """
    token = _get_access_token()
    url = (
        f"{settings.SALESFORCE_INSTANCE_URL}"
        f"/services/data/{settings.SALESFORCE_API_VERSION}"
        f"/sobjects/ContentVersion/{content_version_id}/VersionData"
    )

    logger.info("[SALESFORCE] Downloading ContentVersion id=%s", content_version_id)

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120)

    # If 401, the cached token may have expired — clear it and retry once
    if response.status_code == 401:
        logger.warning("[SALESFORCE] 401 on download — refreshing token and retrying")
        _invalidate_token()
        token = _get_access_token()
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120)

    if not response.ok:
        logger.error(
            "[SALESFORCE] ContentVersion download failed: id=%s status=%s body=%s",
            content_version_id, response.status_code, response.text[:500])
        response.raise_for_status()

    content = response.content
    logger.info(
        "[SALESFORCE] Downloaded ContentVersion id=%s size=%s bytes",
        content_version_id, len(content))

    return content