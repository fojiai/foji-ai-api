"""
Provider router — thin facade over ModelSelectorService.

Phase 1: random selection from all active AIModel rows in DB.
Phase 2 (future): pass agent.preferred_model_id to select a specific model.
  No changes needed here — just add the optional param to select().
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.base import AIProvider
from app.services.model_selector import ModelSelectorService


class ProviderRouter:
    async def select(self, db: AsyncSession) -> AIProvider:
        """Return a fully initialised provider, model_id sourced from DB."""
        return await ModelSelectorService(db).select()
