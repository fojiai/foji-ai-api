"""
Read-only SQLAlchemy mirror of FojiApi's Agent table.
This service never runs migrations — FojiApi owns the schema.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Agent(Base):
    __tablename__ = "Agents"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column("CompanyId", Integer, ForeignKey("Companies.Id"))
    name: Mapped[str] = mapped_column("Name", String(200))
    description: Mapped[str | None] = mapped_column("Description", String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean, default=True)
    industry_type: Mapped[str] = mapped_column("IndustryType", String(30))
    system_prompt: Mapped[str] = mapped_column("SystemPrompt", Text)
    user_prompt: Mapped[str | None] = mapped_column("UserPrompt", Text, nullable=True)
    agent_language: Mapped[str] = mapped_column("AgentLanguage", String(10))
    agent_token: Mapped[str] = mapped_column("AgentToken", String(64), unique=True, index=True)
    whats_app_enabled: Mapped[bool] = mapped_column("WhatsAppEnabled", Boolean, default=False)
    whats_app_phone_number_id: Mapped[str | None] = mapped_column("WhatsAppPhoneNumberId", String, nullable=True)

    # Escalation contacts (plan-gated; injected into system prompt when set)
    support_whats_app_number: Mapped[str | None] = mapped_column("SupportWhatsAppNumber", String(30), nullable=True)
    sales_whats_app_number: Mapped[str | None] = mapped_column("SalesWhatsAppNumber", String(30), nullable=True)
    support_email: Mapped[str | None] = mapped_column("SupportEmail", String(200), nullable=True)
    sales_email: Mapped[str | None] = mapped_column("SalesEmail", String(200), nullable=True)

    # Widget customization
    welcome_message: Mapped[str | None] = mapped_column("WelcomeMessage", String(500), nullable=True)
    conversation_starters: Mapped[str | None] = mapped_column("ConversationStarters", String(2000), nullable=True)
    widget_primary_color: Mapped[str | None] = mapped_column("WidgetPrimaryColor", String(9), nullable=True)
    widget_title: Mapped[str | None] = mapped_column("WidgetTitle", String(100), nullable=True)
    widget_placeholder: Mapped[str | None] = mapped_column("WidgetPlaceholder", String(200), nullable=True)
    widget_position: Mapped[str | None] = mapped_column("WidgetPosition", String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="agents", lazy="select")
    files: Mapped[list["AgentFile"]] = relationship("AgentFile", back_populates="agent", lazy="select")
