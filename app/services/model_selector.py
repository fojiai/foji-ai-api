"""
DB-first model selection.

Queries the AIModels table for all active models, picks one at random,
and instantiates the correct provider with the DB-supplied model_id.

Fallback model IDs are only used if the table returns nothing —
this should never happen in a properly seeded environment.
"""

import logging
import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ProviderException
from app.models.ai_model import AIModel
from app.providers.base import AIProvider
from app.providers.bedrock_provider import BedrockProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# Last-resort fallbacks — only used when AIModels table is empty.
# Update the DB seed data (FojiApi), not these constants.
_FALLBACK_MODEL_IDS = {
    "OpenAi": "gpt-5.4-nano",
    "Gemini": "gemini-flash-lite-latest",
    "Bedrock": "amazon.nova-2-lite-v1:0",
}

_PROVIDER_MAP: dict[str, type] = {
    "OpenAi": OpenAIProvider,
    "Gemini": GeminiProvider,
    "Bedrock": BedrockProvider,
}


class ModelSelectorService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def select(self) -> AIProvider:
        """
        Load all active models from DB → pick one at random → instantiate provider.
        Falls back to a single hardcoded default per provider if the table is empty.
        """
        result = await self._db.execute(
            select(AIModel).where(AIModel.is_active == True)  # noqa: E712
        )
        models: list[AIModel] = list(result.scalars().all())

        if models:
            chosen: AIModel = random.choice(models)
            provider_cls = _PROVIDER_MAP.get(chosen.provider)
            if provider_cls is None:
                raise ProviderException(f"Unknown provider in AIModels table: '{chosen.provider}'")
            logger.debug("Selected model '%s' (%s) from DB", chosen.model_id, chosen.provider)
            return provider_cls(model_id=chosen.model_id)

        # DB returned nothing — use fallback (log loudly so it's noticed)
        logger.warning(
            "AIModels table returned no active models — using hardcoded fallback. "
            "Seed the database to fix this."
        )
        provider_name = random.choice(list(_FALLBACK_MODEL_IDS.keys()))
        model_id = _FALLBACK_MODEL_IDS[provider_name]
        provider_cls = _PROVIDER_MAP[provider_name]
        return provider_cls(model_id=model_id)
