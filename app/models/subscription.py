"""
Read-only SQLAlchemy mirror of FojiApi's Subscription table.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Subscription(Base):
    __tablename__ = "Subscriptions"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column("CompanyId", Integer, ForeignKey("Companies.Id"))
    plan_id: Mapped[int] = mapped_column("PlanId", Integer, ForeignKey("Plans.Id"))
    status: Mapped[str] = mapped_column("Status", String(20))
    current_period_start: Mapped[datetime | None] = mapped_column("CurrentPeriodStart", DateTime, nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column("CurrentPeriodEnd", DateTime, nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column("TrialEndsAt", DateTime, nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column("CanceledAt", DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)

    # Relationships
    plan: Mapped["Plan"] = relationship("Plan", back_populates="subscriptions", lazy="select")
