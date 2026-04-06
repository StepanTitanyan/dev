import hmac
import hashlib
import logging
from datetime import datetime, timezone
from rise.config.config import settings

logger = logging.getLogger(__name__)


def _compute_effective_token(base_token: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hmac.new(base_token.encode(), date_str.encode(), hashlib.sha256).hexdigest()


def verify_api_token(token: str) -> bool:
    base_token = settings.API_BASE_TOKEN
    if not base_token:
        logger.error("API_BASE_TOKEN is not set")
        return False
    expected = _compute_effective_token(base_token)
    return hmac.compare_digest(expected, token)