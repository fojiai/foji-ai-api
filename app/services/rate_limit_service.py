"""
RateLimitService — enforces monthly conversation and message caps.

Logic:
  1. Find the company's active or trialing subscription → Plan limits.
  2. If limits are 0, they are unlimited — no enforcement.
  3. Sum DailyStats for the current calendar month.
  4. Raise RateLimitExceededException if over cap.

Note: DailyStats are populated nightly by the analytics Lambda, so enforcement
lags by up to 24 hours. This is acceptable for v1 and avoids real-time DB writes
on every message.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_stat import DailyStat
from app.models.plan import Plan
from app.models.subscription import Subscription

logger = logging.getLogger(__name__)

# Subscription statuses that have an active plan
_ACTIVE_STATUSES = {"Active", "Trialing"}


class RateLimitExceededException(Exception):
    """Raised when a company has exceeded its monthly usage cap."""

    def __init__(self, resource: str, used: int, limit: int) -> None:
        self.resource = resource
        self.used = used
        self.limit = limit
        super().__init__(f"Monthly {resource} limit reached ({used}/{limit}).")


class RateLimitService:
    async def check(self, db: AsyncSession, company_id: int, is_new_session: bool) -> None:
        """
        Validates that the company has not exceeded its monthly limits.

        Args:
            db: async DB session
            company_id: company to check
            is_new_session: True if this is the first message of a new conversation
        """
        plan = await self._get_active_plan(db, company_id)
        if plan is None:
            # No active subscription — let the chat proceed; billing enforcement
            # is handled by FojiApi at agent creation time, not here.
            return

        max_conv = plan.max_conversations_per_month
        max_msg = plan.max_messages_per_month

        # 0 = unlimited
        if max_conv == 0 and max_msg == 0:
            return

        sessions_used, messages_used = await self._monthly_usage(db, company_id)

        if max_conv > 0 and is_new_session and sessions_used >= max_conv:
            logger.warning(
                "Rate limit: company_id=%s sessions %d/%d",
                company_id, sessions_used, max_conv,
            )
            raise RateLimitExceededException("conversations", sessions_used, max_conv)

        if max_msg > 0 and messages_used >= max_msg:
            logger.warning(
                "Rate limit: company_id=%s messages %d/%d",
                company_id, messages_used, max_msg,
            )
            raise RateLimitExceededException("messages", messages_used, max_msg)

    async def _get_active_plan(self, db: AsyncSession, company_id: int) -> Plan | None:
        today = date.today()
        result = await db.execute(
            select(Subscription)
            .where(
                Subscription.company_id == company_id,
                Subscription.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            return None

        result = await db.execute(select(Plan).where(Plan.id == sub.plan_id))
        return result.scalar_one_or_none()

    async def _monthly_usage(
        self, db: AsyncSession, company_id: int
    ) -> tuple[int, int]:
        """Returns (total_sessions, total_messages) for the current calendar month."""
        today = date.today()
        month_start = today.replace(day=1)

        result = await db.execute(
            select(
                func.coalesce(func.sum(DailyStat.sessions), 0),
                func.coalesce(func.sum(DailyStat.messages), 0),
            ).where(
                DailyStat.company_id == company_id,
                DailyStat.stat_date >= month_start,
                DailyStat.stat_date <= today,
            )
        )
        row = result.one()
        return int(row[0]), int(row[1])
