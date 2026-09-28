from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Company(Base):
    __tablename__ = "Companies"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    name: Mapped[str] = mapped_column("Name", String(200))
    # Nome fantasia — what customers actually know the business as. Preferred
    # over the legal name whenever the agent introduces the company.
    trade_name: Mapped[str | None] = mapped_column("TradeName", String(200), nullable=True)
    slug: Mapped[str] = mapped_column("Slug", String(100), unique=True)
    description: Mapped[str | None] = mapped_column("Description", String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)

    # Relationships
    agents: Mapped[list["Agent"]] = relationship("Agent", back_populates="company")
