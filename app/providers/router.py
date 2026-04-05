"""
Provider router — facade over ModelSelectorService with automatic failover.

Shuffles all active models and tries each one in order. If one provider fails,
the next one is tried automatically. Only fails if all providers error out.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.base import AIProvider
from app.services.model_selector import ModelSelectorService


class ProviderRouter:
    async def select(self, db: AsyncSession) -> AIProvider:
        """Return a single provider (legacy, no failover)."""
        return await ModelSelectorService(db).select()

    async def select_all(self, db: AsyncSession) -> list[AIProvider]:
        """Return all active providers in shuffled order for failover."""
        return await ModelSelectorService(db).select_all()
