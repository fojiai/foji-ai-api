from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PlatformSetting(Base):
    __tablename__ = "PlatformSettings"

    Id: Mapped[int] = mapped_column("Id", Integer, primary_key=True)
    Key: Mapped[str] = mapped_column("Key", String(100), unique=True, nullable=False)
    Value: Mapped[str] = mapped_column("Value", String(2000), nullable=False)
    IsSecret: Mapped[bool] = mapped_column("IsSecret", Boolean, default=True)
    Label: Mapped[str] = mapped_column("Label", String(200), default="")
    Category: Mapped[str] = mapped_column("Category", String(50), default="")
    CreatedAt: Mapped[str] = mapped_column("CreatedAt", DateTime, server_default=func.now())
    UpdatedAt: Mapped[str] = mapped_column("UpdatedAt", DateTime, server_default=func.now())
