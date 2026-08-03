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

# PastDue is Stripe's dunning window — the card failed but retries are still
# pending. Keep serving (a transient payment glitch shouldn't instantly break a
# paying customer's widget) but still enforce that plan's limits.
_GRACE_STATUSES = {"PastDue"}

# Anything else (Canceled, Unpaid, or no subscription row at all) is not served.
_SERVING_STATUSES = _ACTIVE_STATUSES | _GRACE_STATUSES


class RateLimitExceededException(Exception):
    """Raised when a company has exceeded its monthly usage cap."""

    def __init__(self, resource: str, used: int, limit: int) -> None:
        self.resource = resource
        self.used = used
        self.limit = limit
        super().__init__(f"Monthly {resource} limit reached ({used}/{limit}).")


class SubscriptionInactiveException(Exception):
    """Raised when a company has no subscription entitled to be served."""

    def __init__(self, company_id: int) -> None:
        self.company_id = company_id
        super().__init__("No active subscription for this account.")


class RateLimitService:
    async def check(self, db: AsyncSession, company_id: int, is_new_session: bool) -> None:
        """
        Validates that the company has not exceeded its monthly limits.

        Args:
            db: async DB session
            company_id: company to check
            is_new_session: True if this is the first message of a new conversation
        """
        plan = await self.require_serving_plan(db, company_id)

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

    async def require_serving_plan(self, db: AsyncSession, company_id: int) -> Plan:
        """
        Returns the plan the company is entitled to be served under, or raises.

        Fails closed: nothing else revokes serving capacity when a subscription is
        canceled or a trial expires — those paths only flip Subscription.Status —
        so treating "no plan" as "allow" handed every churned account unlimited
        free inference indefinitely.
        """
        plan = await self._get_active_plan(db, company_id)
        if plan is None:
            logger.warning(
                "Serving denied: company_id=%s has no active subscription", company_id
            )
            raise SubscriptionInactiveException(company_id)
        return plan

    async def _get_active_plan(self, db: AsyncSession, company_id: int) -> Plan | None:
        result = await db.execute(
            select(Subscription)
            .where(
                Subscription.company_id == company_id,
                Subscription.status.in_(_SERVING_STATUSES),
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
