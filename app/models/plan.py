"""
Read-only SQLAlchemy mirror of FojiApi's Plan table.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Plan(Base):
    __tablename__ = "Plans"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    name: Mapped[str] = mapped_column("Name", String(100))
    slug: Mapped[str] = mapped_column("Slug", String(100))
    max_agents: Mapped[int] = mapped_column("MaxAgents", Integer)
    has_whats_app: Mapped[bool] = mapped_column("HasWhatsApp", Boolean, default=False)
    has_escalation_contacts: Mapped[bool] = mapped_column("HasEscalationContacts", Boolean, default=False)
    max_conversations_per_month: Mapped[int] = mapped_column("MaxConversationsPerMonth", Integer, default=0)
    max_messages_per_month: Mapped[int] = mapped_column("MaxMessagesPerMonth", Integer, default=0)
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)

    # Relationships
    subscriptions: Mapped[list["Subscription"]] = relationship("Subscription", back_populates="plan")
