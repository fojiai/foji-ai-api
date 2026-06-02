"""
SQLAlchemy model for the Leads table.
FojiApi owns the schema/migrations — this model is used by foji-ai-api to write lead records
captured by the widget.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Lead(Base):
    __tablename__ = "Leads"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column("AgentId", Integer, ForeignKey("Agents.Id"))
    company_id: Mapped[int] = mapped_column("CompanyId", Integer, ForeignKey("Companies.Id"))
    name: Mapped[str | None] = mapped_column("Name", String(200), nullable=True)
    email: Mapped[str | None] = mapped_column("Email", String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column("Phone", String(30), nullable=True)
    session_id: Mapped[str] = mapped_column("SessionId", String(64))
    source: Mapped[str] = mapped_column("Source", String(20), default="widget")
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)
