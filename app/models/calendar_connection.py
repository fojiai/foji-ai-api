from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AgentCalendarConnection(Base):
    __tablename__ = "AgentCalendarConnections"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column("AgentId", Integer, ForeignKey("Agents.Id"))
    google_account_email: Mapped[str] = mapped_column("GoogleAccountEmail", String(254))
    encrypted_refresh_token: Mapped[str] = mapped_column("EncryptedRefreshToken", Text)
    is_active: Mapped[bool] = mapped_column("IsActive", Boolean, default=True)
    connected_at: Mapped[datetime] = mapped_column("ConnectedAt", DateTime)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)
