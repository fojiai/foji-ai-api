"""
Credential resolution: DB (PlatformSettings) first, env vars as fallback.

Caches DB values for 60 seconds to avoid per-request queries.
"""

import logging
import time
from typing import Optional

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models.platform_setting import PlatformSetting

logger = logging.getLogger(__name__)

_cache: dict[str, str] = {}
_cache_ts: float = 0
_CACHE_TTL = 60  # seconds


async def _refresh_cache() -> None:
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL:
        return
    try:
        async with get_session_factory()() as session:
            result = await session.execute(select(PlatformSetting))
            rows = result.scalars().all()
            _cache = {row.Key: row.Value for row in rows if row.Value}
            _cache_ts = now
            logger.debug("Refreshed credentials cache: %d keys", len(_cache))
    except Exception:
        logger.exception("Failed to refresh credentials from DB")


async def get_credential(key: str) -> str:
    """Get a credential value. Checks DB first, then env vars."""
    await _refresh_cache()

    # DB value takes priority
    db_value = _cache.get(key, "")
    if db_value:
        return db_value

    # Fall back to env var via settings
    settings = get_settings()
    env_map: dict[str, str] = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "GEMINI_API_KEY": settings.gemini_api_key,
        "AWS_ACCESS_KEY_ID": settings.aws_access_key_id,
        "AWS_SECRET_ACCESS_KEY": settings.aws_secret_access_key,
        "AWS_BEDROCK_REGION": settings.aws_bedrock_region,
    }
    return env_map.get(key, "")


def invalidate_cache() -> None:
    """Force next call to re-read from DB."""
    global _cache_ts
    _cache_ts = 0
