"""
Read-only SQLAlchemy mirror of FojiApi's DailyStats table.
Used by RateLimitService to check monthly usage.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyStat(Base):
    __tablename__ = "DailyStats"

    id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column("CompanyId", Integer, ForeignKey("Companies.Id"))
    stat_date: Mapped[date] = mapped_column("StatDate", Date)
    sessions: Mapped[int] = mapped_column("Sessions", Integer, default=0)
    messages: Mapped[int] = mapped_column("Messages", Integer, default=0)
    input_tokens: Mapped[int] = mapped_column("InputTokens", BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column("OutputTokens", BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column("CreatedAt", DateTime)
    updated_at: Mapped[datetime] = mapped_column("UpdatedAt", DateTime)
